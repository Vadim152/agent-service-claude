package ru.sber.aitestplugin.ui.toolwindow

import com.fasterxml.jackson.databind.DeserializationFeature
import com.fasterxml.jackson.databind.SerializationFeature
import com.fasterxml.jackson.datatype.jsr310.JavaTimeModule
import com.fasterxml.jackson.module.kotlin.jacksonObjectMapper
import com.fasterxml.jackson.module.kotlin.readValue
import com.intellij.icons.AllIcons
import com.intellij.openapi.application.ApplicationManager
import com.intellij.openapi.diagnostic.Logger
import com.intellij.openapi.options.ShowSettingsUtil
import com.intellij.openapi.project.Project
import com.intellij.openapi.util.text.StringUtil
import com.intellij.openapi.ui.popup.JBPopup
import com.intellij.openapi.ui.popup.JBPopupFactory
import com.intellij.openapi.ui.popup.JBPopupListener
import com.intellij.openapi.ui.popup.LightweightWindowEvent
import com.intellij.openapi.ui.popup.PopupStep
import com.intellij.openapi.ui.popup.util.BaseListPopupStep
import com.intellij.ui.JBColor
import com.intellij.ui.awt.RelativePoint
import com.intellij.ui.components.JBLabel
import com.intellij.ui.components.JBList
import com.intellij.ui.components.JBScrollPane
import com.intellij.ui.components.JBTextArea
import com.intellij.util.concurrency.AppExecutorUtil
import com.intellij.util.ui.JBUI
import okhttp3.Call
import okhttp3.OkHttpClient
import okhttp3.Request
import ru.sber.aitestplugin.config.AiTestPluginSettingsConfigurable
import ru.sber.aitestplugin.config.AiTestPluginSettingsService
import ru.sber.aitestplugin.config.toJiraInstanceUrl
import ru.sber.aitestplugin.config.toZephyrAuthDto
import ru.sber.aitestplugin.model.ChatCommandRequestDto
import ru.sber.aitestplugin.model.ChatHistoryResponseDto
import ru.sber.aitestplugin.model.ChatMessageRequestDto
import ru.sber.aitestplugin.model.ChatPendingPermissionDto
import ru.sber.aitestplugin.model.ChatSessionCreateRequestDto
import ru.sber.aitestplugin.model.ChatSessionListItemDto
import ru.sber.aitestplugin.model.ChatSessionStatusResponseDto
import ru.sber.aitestplugin.model.ChatToolDecisionRequestDto
import ru.sber.aitestplugin.model.RunArtifactDto
import ru.sber.aitestplugin.model.RunEventResponseDto
import ru.sber.aitestplugin.model.ScanStepsResponseDto
import ru.sber.aitestplugin.model.UnmappedStepDto
import ru.sber.aitestplugin.services.BackendClient
import ru.sber.aitestplugin.services.HttpBackendClient
import ru.sber.aitestplugin.ui.UiStrings
import ru.sber.aitestplugin.ui.components.StatusBadge
import ru.sber.aitestplugin.ui.theme.PluginUiTheme
import ru.sber.aitestplugin.ui.theme.PluginUiTokens
import ru.sber.aitestplugin.ui.toolwindow.components.ChatComposerPanel
import ru.sber.aitestplugin.ui.toolwindow.components.HistoryPanel
import ru.sber.aitestplugin.ui.toolwindow.components.RunDetailsPanel
import ru.sber.aitestplugin.ui.toolwindow.components.ToolWindowHeaderPanel
import java.awt.BorderLayout
import java.awt.CardLayout
import java.awt.Color
import java.awt.Component
import java.awt.Cursor
import java.awt.Dimension
import java.awt.FlowLayout
import java.awt.Font
import java.awt.Graphics
import java.awt.Graphics2D
import java.awt.Insets
import java.awt.RenderingHints
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.Locale
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import javax.swing.BorderFactory
import javax.swing.BoxLayout
import javax.swing.DefaultListCellRenderer
import javax.swing.DefaultListModel
import javax.swing.JButton
import javax.swing.JComboBox
import javax.swing.JList
import javax.swing.JPanel
import javax.swing.JSplitPane
import javax.swing.SwingUtilities
import javax.swing.Timer
import javax.swing.UIManager
import javax.swing.border.AbstractBorder
import javax.swing.event.DocumentEvent
import javax.swing.event.DocumentListener

class AiToolWindowPanel(
    private val project: Project,
    private val backendClient: BackendClient = HttpBackendClient(project)
) : JPanel(BorderLayout()) {
    private val logger = Logger.getInstance(AiToolWindowPanel::class.java)
    private val settings = AiTestPluginSettingsService.getInstance(project).settings
    private val refreshInFlight = AtomicBoolean(false)
    private val streamClient = OkHttpClient.Builder().readTimeout(0, TimeUnit.MILLISECONDS).build()
    private val jsonMapper = jacksonObjectMapper()
        .registerModule(JavaTimeModule())
        .disable(SerializationFeature.WRITE_DATES_AS_TIMESTAMPS)
        .disable(DeserializationFeature.FAIL_ON_UNKNOWN_PROPERTIES)
    private val pollTimer = Timer(3000) { refreshControlPlaneAsync() }
    private val uiRefreshDebounceMs = 200
    private val autoScrollBottomThresholdPx = 48
    private val timeFormatter = DateTimeFormatter.ofPattern("HH:mm").withZone(ZoneId.systemDefault())
    private val supportedCommands = listOf("status", "diff", "compact", "abort", "help")
    private val slashTemplates = listOf(
        SlashTemplateItem(
            key = "autotest",
            title = "Сгенерировать автотест",
            text = "Сгенерируй автотест по тесткейсу ниже и покажи preview feature + pipeline."
        ),
        SlashTemplateItem(
            key = "unmapped",
            title = "Проанализировать unmapped",
            text = "Проанализируй unmapped шаги и предложи варианты сопоставления."
        ),
        SlashTemplateItem(
            key = "save",
            title = "Сгенерировать и сохранить",
            text = "Сгенерируй автотест и предложи сохранить в targetPath=src/test/resources/features/generated.feature."
        )
    )
    private val sseIndexPattern = Regex("\"index\"\\s*:\\s*(\\d+)")
    private val theme = PluginUiTheme

    private val cardLayout = CardLayout()
    private val bodyCards = JPanel(cardLayout)
    private val timelineLines = mutableListOf<UiLine>()
    private val timelineContainer = JPanel().apply {
        layout = BoxLayout(this, BoxLayout.Y_AXIS)
        isOpaque = false
    }
    private val historyModel = DefaultListModel<ChatSessionListItemDto>()
    private val historyList = JBList(historyModel)

    private val inputArea = JBTextArea(4, 20)
    private val sendButton = JButton()
    private val runtimeSelector = JComboBox(RuntimeMode.values())
    private val runInfoLabel = JBLabel().apply { foreground = theme.secondaryText }
    private val runArtifactsModel = DefaultListModel<RunArtifactDto>()
    private val runArtifactsList = JBList(runArtifactsModel)
    private val artifactPreviewLabel = JBLabel().apply { foreground = theme.secondaryText }
    private val artifactPreviewArea = JBTextArea().apply {
        isEditable = false
        lineWrap = true
        wrapStyleWord = true
        isOpaque = false
        foreground = theme.primaryText
        caretColor = theme.primaryText
        border = JBUI.Borders.empty(8, 10)
        text = UiStrings.runArtifactsPlaceholder
    }
    private val runArtifactsPanel = JPanel(BorderLayout()).apply {
        isOpaque = true
        background = theme.containerBackground
        border = JBUI.Borders.compound(
            BorderFactory.createLineBorder(theme.containerBorder, 1, true),
            JBUI.Borders.empty(6, 8)
        )
        isVisible = false
    }
    private val runEventsModel = DefaultListModel<LiveRunEventItem>()
    private val runEventsList = JBList(runEventsModel)
    private val runEventPreviewLabel = JBLabel().apply { foreground = theme.secondaryText }
    private val runEventPreviewArea = JBTextArea().apply {
        isEditable = false
        lineWrap = true
        wrapStyleWord = true
        isOpaque = false
        foreground = theme.primaryText
        caretColor = theme.primaryText
        border = JBUI.Borders.empty(8, 10)
        text = UiStrings.runEventsPlaceholder
    }
    private val runEventsPanel = JPanel(BorderLayout()).apply {
        isOpaque = true
        background = theme.containerBackground
        border = JBUI.Borders.compound(
            BorderFactory.createLineBorder(theme.containerBorder, 1, true),
            JBUI.Borders.empty(6, 8)
        )
        isVisible = false
    }
    private val runInfoPanel = JPanel(BorderLayout()).apply {
        isOpaque = true
        background = theme.containerBackground
        border = JBUI.Borders.compound(
            BorderFactory.createLineBorder(theme.containerBorder, 1, true),
            JBUI.Borders.empty(6, 8)
        )
        isVisible = false
        add(runInfoLabel, BorderLayout.CENTER)
    }
    private val statusLabel = JBLabel(UiStrings.connecting)
    private val statusBadge = StatusBadge(UiStrings.connecting, false)

    private val approvalPanel = JPanel().apply {
        layout = BoxLayout(this, BoxLayout.Y_AXIS)
        isOpaque = false
        border = JBUI.Borders.empty(6, 8, 4, 8)
    }
    private val uiApplyTimer = Timer(uiRefreshDebounceMs) { applyPendingUiRefresh() }.apply { isRepeats = false }

    private var selectedRuntime: RuntimeMode = RuntimeMode.CHAT
    private var sessionId: String? = null
    private val sessionIdsByRuntime = mutableMapOf<RuntimeMode, String>()
    private var streamSessionId: String? = null
    private var streamCall: Call? = null
    private var runEventsStreamRunId: String? = null
    private var runEventsStreamCall: Call? = null
    private var runEventsFromIndex: Int = 0
    private var slashPopup: JBPopup? = null
    private var isApplyingSlashSelection: Boolean = false
    private var suppressSlashPopupUntilReset: Boolean = false
    private var lastSlashMatches: List<String> = emptyList()
    private var latestActivity: String = "idle"
    private var connectionState: ConnectionState = ConnectionState.CONNECTING
    private var connectionDetails: String? = null
    private var streamReconnectAttempt: Int = 0
    private var streamFromIndex: Int = 0
    private var timelineScrollPane: JBScrollPane? = null
    private var pendingHistoryForRender: ChatHistoryResponseDto? = null
    private var pendingStatusForRender: ChatSessionStatusResponseDto? = null
    @Volatile
    private var initialSessionRequested: Boolean = false
    @Volatile
    private var initialSessionReady: Boolean = false
    @Volatile
    private var forceScrollToBottom: Boolean = false
    private val artifactsRefreshInFlight = AtomicBoolean(false)
    private val sessionStateLock = Any()
    private var lastRenderedServerTailKey: String? = null
    private var currentArtifactRunId: String? = null
    private var selectedArtifactKey: String? = null
    private var selectedRunEventIndex: Int? = null
    private var lastAgentRunId: String? = null
    private var lastArtifactsRefreshAtMs: Long = 0L

    init {
        border = JBUI.Borders.empty(8, 8, 10, 8)
        background = theme.panelBackground
        isOpaque = true
        add(buildRoot(), BorderLayout.CENTER)
        updateStatusLabel()
        initialSessionRequested = true
        ensureSessionAsync(forceNew = true)
    }

    override fun addNotify() {
        super.addNotify()
        pollTimer.start()
        sessionId?.let { startEventStreamAsync(it) }
    }

    override fun removeNotify() {
        pollTimer.stop()
        uiApplyTimer.stop()
        pendingHistoryForRender = null
        pendingStatusForRender = null
        stopEventStream()
        stopRunEventStream()
        suppressSlashPopupUntilReset = false
        lastSlashMatches = emptyList()
        hideSlashPopup()
        super.removeNotify()
    }

    fun showScanResult(response: ScanStepsResponseDto) {
        appendSystemLine("Сканирование завершено: steps=${response.stepsCount}, updated=${response.updatedAt}.")
    }

    fun showUnmappedSteps(unmappedSteps: List<UnmappedStepDto>) {
        if (unmappedSteps.isNotEmpty()) {
            appendSystemLine("Несопоставленные шаги: ${unmappedSteps.size}")
        }
    }

    private fun buildRoot(): JPanel {
        return JPanel(BorderLayout()).apply {
            isOpaque = true
            background = theme.panelBackground
            add(buildHeader(), BorderLayout.NORTH)
            add(buildBody(), BorderLayout.CENTER)
            add(buildInput(), BorderLayout.SOUTH)
        }
    }

    private fun buildHeader(): JPanel = ToolWindowHeaderPanel(
        title = ToolWindowIds.DISPLAY_NAME,
        statusComponent = statusBadge,
        onNewSession = { ensureSessionAsync(forceNew = true) },
        onShowHistory = {
            showHistoryScreen()
            loadSessionsHistoryAsync()
        },
        onOpenSettings = {
            ShowSettingsUtil.getInstance().showSettingsDialog(
                project,
                AiTestPluginSettingsConfigurable::class.java
            )
        }
    )

    private fun buildBody(): JPanel {
        bodyCards.isOpaque = false
        bodyCards.add(buildChatCard(), "chat")
        bodyCards.add(buildHistoryCard(), "history")
        cardLayout.show(bodyCards, "chat")
        return bodyCards
    }

    private fun buildChatCard(): JPanel {
        val timelineViewport = JPanel(BorderLayout()).apply {
            isOpaque = false
            add(timelineContainer, BorderLayout.NORTH)
        }
        val footer = JPanel(BorderLayout()).apply {
            isOpaque = false
            add(RunDetailsPanel(runInfoPanel, runEventsPanel, runArtifactsPanel, approvalPanel), BorderLayout.CENTER)
        }
        configureRunEventsPanel()
        configureRunArtifactsPanel()
        renderTimeline()

        return JPanel(BorderLayout()).apply {
            isOpaque = false
            add(JBScrollPane(timelineViewport).apply {
                border = JBUI.Borders.compound(
                    BorderFactory.createLineBorder(theme.containerBorder, 1, true),
                    JBUI.Borders.empty(2)
                )
                background = theme.panelBackground
                viewport.background = theme.panelBackground
                preferredSize = Dimension(100, PluginUiTokens.toolWindowMinTimelineHeight)
                viewport.addComponentListener(object : java.awt.event.ComponentAdapter() {
                    override fun componentResized(e: java.awt.event.ComponentEvent?) {
                        renderTimeline()
                    }
                })
                timelineScrollPane = this
            }, BorderLayout.CENTER)
            add(footer, BorderLayout.SOUTH)
        }
    }

    private fun buildHistoryCard(): JPanel {
        historyList.cellRenderer = SessionRenderer(timeFormatter)
        historyList.background = theme.containerBackground
        historyList.foreground = theme.primaryText
        historyList.selectionBackground = theme.controlBackground
        historyList.selectionForeground = theme.primaryText
        historyList.emptyText.text = UiStrings.noChatsYet
        historyList.addMouseListener(object : java.awt.event.MouseAdapter() {
            override fun mouseClicked(e: java.awt.event.MouseEvent) {
                if (e.clickCount >= 2) {
                    historyList.selectedValue?.let { activateSession(it) }
                }
            }
        })

        return HistoryPanel(
            historyList = historyList,
            onBack = { showChatScreen() },
            onOpenSelected = { historyList.selectedValue?.let { activateSession(it) } }
        )
    }

    private fun buildInput(): JPanel {
        inputArea.lineWrap = true
        inputArea.wrapStyleWord = true
        inputArea.background = theme.inputBackground
        inputArea.foreground = theme.primaryText
        inputArea.caretColor = theme.primaryText
        inputArea.border = JBUI.Borders.empty(4, 6)
        inputArea.font = inputArea.font.deriveFont(14f)
        inputArea.putClientProperty("JTextArea.placeholderText", UiStrings.chatInputPlaceholder)
        inputArea.document.addDocumentListener(object : DocumentListener {
            override fun insertUpdate(e: DocumentEvent?) = maybeShowSlashPopup()
            override fun removeUpdate(e: DocumentEvent?) = maybeShowSlashPopup()
            override fun changedUpdate(e: DocumentEvent?) = maybeShowSlashPopup()
        })
        inputArea.addKeyListener(object : java.awt.event.KeyAdapter() {
            override fun keyPressed(e: java.awt.event.KeyEvent) {
                if (e.keyCode == java.awt.event.KeyEvent.VK_ENTER && !e.isShiftDown) {
                    e.consume()
                    onSendOrStop()
                }
            }
        })

        sendButton.cursor = Cursor.getPredefinedCursor(Cursor.HAND_CURSOR)
        sendButton.preferredSize = Dimension(42, 34)
        sendButton.border = RoundedLineBorder(theme.controlBorder, 1, 14)
        sendButton.isBorderPainted = true
        sendButton.isFocusPainted = false
        sendButton.isContentAreaFilled = true
        sendButton.addActionListener { onSendOrStop() }
        updateSendButtonState()

        runtimeSelector.selectedItem = selectedRuntime
        runtimeSelector.toolTipText = UiStrings.runtimeLabel
        runtimeSelector.background = theme.controlBackground
        runtimeSelector.foreground = theme.primaryText
        runtimeSelector.border = BorderFactory.createLineBorder(theme.controlBorder, 1, true)
        runtimeSelector.renderer = RuntimeModeRenderer()
        runtimeSelector.addActionListener {
            val target = runtimeSelector.selectedItem as? RuntimeMode ?: return@addActionListener
            if (target == selectedRuntime) return@addActionListener
            selectedRuntime = target
            sessionId = sessionIdsByRuntime[target]
            latestActivity = "idle"
            connectionDetails = null
            stopRunEventStream()
            clearRunEventsPanel()
            clearRunArtifactsPanel()
            updateStatusLabel()
            ensureSessionAsync(forceNew = false)
        }

        statusLabel.foreground = theme.secondaryText
        statusLabel.border = JBUI.Borders.empty(6, 6, 0, 6)
        return ChatComposerPanel(runtimeSelector, inputArea, sendButton, statusLabel)
    }

    private fun onSendOrStop() {
        if (isGenerating()) {
            submitCommand("abort")
        } else {
            submitInput(inputArea.text.trim())
        }
    }

    private fun submitInput(input: String) {
        if (input.isBlank()) return
        submitMessage(input)
    }

    private fun submitMessage(message: String) {
        if (isGenerating()) {
            appendSystemLine("\u0414\u043e\u0436\u0434\u0438\u0442\u0435\u0441\u044c \u0437\u0430\u0432\u0435\u0440\u0448\u0435\u043d\u0438\u044f \u0442\u0435\u043a\u0443\u0449\u0435\u0433\u043e \u043e\u0442\u0432\u0435\u0442\u0430.")
            return
        }
        latestActivity = "busy"
        upsertProgressLine("\u0418\u0434\u0451\u0442 \u043e\u0431\u0440\u0430\u0431\u043e\u0442\u043a\u0430 \u0437\u0430\u043f\u0440\u043e\u0441\u0430...")
        updateSendButtonState()
        updateStatusLabel()
        ApplicationManager.getApplication().executeOnPooledThread {
            val requireFreshSession = sessionId.isNullOrBlank() || (initialSessionRequested && !initialSessionReady)
            val active = ensureSessionBlocking(forceNew = requireFreshSession)
            if (active == null) {
                SwingUtilities.invokeLater {
                    latestActivity = "idle"
                    removeProgressLine()
                    updateSendButtonState()
                    updateStatusLabel()
                }
                return@executeOnPooledThread
            }
            try {
                backendClient.sendChatMessage(active, ChatMessageRequestDto(content = message))
                SwingUtilities.invokeLater {
                    inputArea.text = ""
                    suppressSlashPopupUntilReset = false
                    hideSlashPopup()
                    forceScrollToBottom = true
                    scrollToBottomIfNeeded(true)
                    setConnectionState(ConnectionState.CONNECTED)
                }
                refreshControlPlaneAsync()
            } catch (ex: Exception) {
                logger.warn("Failed to send chat message", ex)
                SwingUtilities.invokeLater {
                    latestActivity = "idle"
                    removeProgressLine()
                    updateSendButtonState()
                    updateStatusLabel()
                    appendSystemLine("\u041d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u043e\u0442\u043f\u0440\u0430\u0432\u0438\u0442\u044c \u0441\u043e\u043e\u0431\u0449\u0435\u043d\u0438\u0435: ${ex.message}")
                }
            }
        }
    }

    private fun submitCommand(command: String) {
        val active = sessionId ?: return
        appendSystemLine("/$command")
        ApplicationManager.getApplication().executeOnPooledThread {
            try {
                backendClient.executeChatCommand(active, ChatCommandRequestDto(command = command))
                refreshControlPlaneAsync()
            } catch (ex: Exception) {
                logger.warn("Failed to execute command", ex)
                SwingUtilities.invokeLater { appendSystemLine("\u041d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u0432\u044b\u043f\u043e\u043b\u043d\u0438\u0442\u044c \u043a\u043e\u043c\u0430\u043d\u0434\u0443: ${ex.message}") }
            }
        }
    }

    private fun ensureSessionAsync(forceNew: Boolean) {
        if (forceNew) {
            initialSessionRequested = true
            initialSessionReady = false
        }
        ApplicationManager.getApplication().executeOnPooledThread {
            val active = ensureSessionBlocking(forceNew) ?: return@executeOnPooledThread
            SwingUtilities.invokeLater {
                showChatScreen()
                setConnectionState(ConnectionState.CONNECTING, "\u0421\u0435\u0441\u0441\u0438\u044f ${active.take(8)}")
            }
            startEventStreamAsync(active)
            refreshControlPlaneAsync()
        }
    }

    private fun currentProjectRoot(): String = project.basePath.orEmpty()

    private fun ensureSessionBlocking(forceNew: Boolean): String? {
        synchronized(sessionStateLock) {
            if (!forceNew) {
                val existing = sessionIdsByRuntime[selectedRuntime]
                if (!existing.isNullOrBlank()) {
                    sessionId = existing
                    initialSessionReady = true
                    return existing
                }
            }
            if (!forceNew && !sessionId.isNullOrBlank()) {
                initialSessionReady = true
                return sessionId
            }
            if (forceNew) {
                initialSessionRequested = true
                initialSessionReady = false
            }

            val projectRoot = currentProjectRoot()
            if (projectRoot.isBlank()) {
                SwingUtilities.invokeLater { setConnectionState(ConnectionState.OFFLINE, "\u041a\u043e\u0440\u0435\u043d\u044c \u043f\u0440\u043e\u0435\u043a\u0442\u0430 \u043d\u0435 \u0443\u043a\u0430\u0437\u0430\u043d") }
                return null
            }

            return try {
                val created = backendClient.createChatSession(
                    ChatSessionCreateRequestDto(
                        projectRoot = projectRoot,
                        source = "ide-plugin",
                        profile = selectedRuntime.defaultProfile,
                        runtime = selectedRuntime.backendValue,
                        reuseExisting = !forceNew,
                        zephyrAuth = settings.toZephyrAuthDto(),
                        jiraInstance = settings.toJiraInstanceUrl()
                    )
                )
                sessionId = created.sessionId
                sessionIdsByRuntime[selectedRuntime] = created.sessionId
                latestActivity = "idle"
                streamReconnectAttempt = 0
                streamFromIndex = 0
                initialSessionReady = true
                if (forceNew || !created.reused) {
                    SwingUtilities.invokeLater {
                        uiApplyTimer.stop()
                        pendingHistoryForRender = null
                        pendingStatusForRender = null
                        forceScrollToBottom = false
                        timelineLines.clear()
                        lastRenderedServerTailKey = null
                        renderTimeline()
                        renderPendingApprovals(emptyList())
                        clearRunArtifactsPanel()
                    }
                }
                created.sessionId
            } catch (ex: Exception) {
                if (forceNew) {
                    initialSessionReady = false
                }
                logger.warn("Failed to create session", ex)
                SwingUtilities.invokeLater { setConnectionState(ConnectionState.OFFLINE, "\u041e\u0448\u0438\u0431\u043a\u0430 \u0438\u043d\u0438\u0446\u0438\u0430\u043b\u0438\u0437\u0430\u0446\u0438\u0438: ${ex.message}") }
                null
            }
        }
    }

    private fun activateSession(targetSession: ChatSessionListItemDto) {
        selectedRuntime = runtimeModeFromBackend(targetSession.runtime)
        runtimeSelector.selectedItem = selectedRuntime
        sessionId = targetSession.sessionId
        sessionIdsByRuntime[selectedRuntime] = targetSession.sessionId
        initialSessionRequested = true
        initialSessionReady = true
        latestActivity = "idle"
        forceScrollToBottom = false
        uiApplyTimer.stop()
        pendingHistoryForRender = null
        pendingStatusForRender = null
        timelineLines.clear()
        lastRenderedServerTailKey = null
        renderTimeline()
        renderPendingApprovals(emptyList())
        clearRunArtifactsPanel()
        streamReconnectAttempt = 0
        streamFromIndex = 0
        showChatScreen()
        startEventStreamAsync(targetSession.sessionId)
        refreshControlPlaneAsync()
    }

    private fun loadSessionsHistoryAsync() {
        val projectRoot = currentProjectRoot()
        if (projectRoot.isBlank()) {
            setConnectionState(ConnectionState.OFFLINE, "\u041a\u043e\u0440\u0435\u043d\u044c \u043f\u0440\u043e\u0435\u043a\u0442\u0430 \u043d\u0435 \u0443\u043a\u0430\u0437\u0430\u043d")
            return
        }
        ApplicationManager.getApplication().executeOnPooledThread {
            try {
                val result = backendClient.listChatSessions(projectRoot, 100)
                SwingUtilities.invokeLater {
                    historyModel.clear()
                    result.items.forEach(historyModel::addElement)
                }
            } catch (ex: Exception) {
                logger.warn("Failed to load sessions", ex)
                SwingUtilities.invokeLater { setConnectionState(ConnectionState.OFFLINE, "\u041d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u0437\u0430\u0433\u0440\u0443\u0437\u0438\u0442\u044c \u0438\u0441\u0442\u043e\u0440\u0438\u044e") }
            }
        }
    }

    private fun refreshControlPlaneAsync() {
        val active = sessionId ?: return
        if (!refreshInFlight.compareAndSet(false, true)) return

        ApplicationManager.getApplication().executeOnPooledThread {
            try {
                val history = backendClient.getChatHistory(active)
                val status = backendClient.getChatStatus(active)
                SwingUtilities.invokeLater {
                    enqueueUiRefresh(history, status)
                }
            } catch (ex: Exception) {
                if (logger.isDebugEnabled) logger.debug("Refresh failed", ex)
            } finally {
                refreshInFlight.set(false)
            }
        }
    }

    private fun enqueueUiRefresh(history: ChatHistoryResponseDto, status: ChatSessionStatusResponseDto) {
        pendingHistoryForRender = history
        pendingStatusForRender = status
        uiApplyTimer.restart()
    }

    private fun applyPendingUiRefresh() {
        val history = pendingHistoryForRender ?: return
        val status = pendingStatusForRender ?: return
        pendingHistoryForRender = null
        pendingStatusForRender = null
        renderHistory(history)
        renderStatus(status)
    }

    private fun renderHistory(history: ChatHistoryResponseDto) {
        val shouldStickToBottom = forceScrollToBottom || isUserNearBottom()
        val seenMessageKeys = mutableSetOf<String>()
        val serverLines = history.messages
            .filterNot { it.role.equals("assistant", ignoreCase = true) && it.content.trim().isBlank() }
            .sortedBy { it.createdAt }
            .filter { message ->
                val key = message.messageId.ifBlank {
                    "${message.role}:${message.createdAt.toEpochMilli()}:${message.content.hashCode()}"
                }
                seenMessageKeys.add(key)
            }
            .map { message ->
                val lineKind = when (message.role.lowercase()) {
                    "user" -> UiLineKind.USER
                    "assistant" -> UiLineKind.ASSISTANT
                    else -> UiLineKind.SYSTEM
                }
                val stableKey = message.messageId.ifBlank {
                    "${message.role}:${message.createdAt.toEpochMilli()}:${message.content.hashCode()}"
                }
                UiLine(
                    kind = lineKind,
                    text = message.content,
                    createdAt = message.createdAt,
                    stableKey = stableKey,
                    source = UiLineSource.SERVER_MESSAGE
                )
            }

        val localLines = timelineLines.filter { it.source == UiLineSource.LOCAL_SYSTEM }
        val progressLine = timelineLines.firstOrNull { it.source == UiLineSource.PROGRESS }

        val targetLines = buildList {
            addAll(serverLines)
            addAll(localLines)
            progressLine?.let { add(it) }
        }
        replaceTimelineModelIncrementally(targetLines)
        renderPendingApprovals(history.pendingPermissions)
        scrollToBottomIfNeeded(shouldStickToBottom)
        val currentTailKey = serverLines.lastOrNull()?.stableKey
        if (forceScrollToBottom && currentTailKey != null && currentTailKey != lastRenderedServerTailKey) {
            forceScrollToBottom = false
        }
        if (currentTailKey != null) {
            lastRenderedServerTailKey = currentTailKey
        }
    }

    private fun renderStatus(status: ChatSessionStatusResponseDto) {
        latestActivity = status.activity.lowercase()
        selectedRuntime = runtimeModeFromBackend(status.runtime)
        runtimeSelector.selectedItem = selectedRuntime
        updateSendButtonState()

        val progress = when (latestActivity) {
            "busy" -> "\u0412 \u0440\u0430\u0431\u043e\u0442\u0435: ${status.currentAction}"
            "retry" -> {
                val retry = status.lastRetryAttempt?.let { "\u041f\u043e\u0432\u0442\u043e\u0440 #$it" } ?: "\u041f\u043e\u0432\u0442\u043e\u0440"
                "$retry: ${status.lastRetryMessage ?: status.currentAction}"
            }
            "waiting_permission" -> "\u041e\u0436\u0438\u0434\u0430\u0435\u0442 \u043f\u043e\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043d\u0438\u044f: ${status.currentAction}"
            else -> null
        }
        if (progress != null) {
            upsertProgressLine(progress)
        } else {
            removeProgressLine()
        }
        val isAgentRuntime = status.runtime == RuntimeMode.AGENT.backendValue
        val runIdForPanel = if (isAgentRuntime) {
            status.activeRunId?.takeIf { it.isNotBlank() }?.also { lastAgentRunId = it } ?: lastAgentRunId
        } else {
            null
        }
        if (runIdForPanel != null) {
            val statusText = status.activeRunStatus ?: if (latestActivity == "idle") "ready" else "running"
            runInfoLabel.text = "Run ${runIdForPanel.take(8)} | $statusText | ${status.currentAction}"
            runInfoPanel.isVisible = true
            runEventsPanel.isVisible = true
            ensureRunEventStream(runIdForPanel)
            runArtifactsPanel.isVisible = true
            refreshRunArtifactsAsync(runIdForPanel)
            if (status.activeRunStatus?.lowercase() in setOf("succeeded", "failed", "cancelled")) {
                stopRunEventStream()
            }
        } else {
            runInfoPanel.isVisible = false
            stopRunEventStream()
            clearRunEventsPanel()
            clearRunArtifactsPanel()
        }
        updateStatusLabel()
    }

    private fun renderPendingApprovals(pending: List<ChatPendingPermissionDto>) {
        approvalPanel.removeAll()
        pending.forEach { permission ->
            val row = JPanel(BorderLayout()).apply {
                border = JBUI.Borders.compound(
                    BorderFactory.createLineBorder(theme.containerBorder, 1, true),
                    JBUI.Borders.empty(8)
                )
                background = theme.containerBackground
                isOpaque = true
            }
            row.add(JBLabel("\u041f\u043e\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043d\u0438\u0435: ${permission.title} (${permission.kind})").apply {
                foreground = theme.primaryText
            }, BorderLayout.CENTER)
            row.add(JPanel(FlowLayout(FlowLayout.RIGHT, 6, 0)).apply {
                isOpaque = false
                add(actionButton("\u0420\u0430\u0437\u0440\u0435\u0448\u0438\u0442\u044c \u043e\u0434\u0438\u043d \u0440\u0430\u0437") { submitApproval(permission, "approve_once") })
                add(actionButton("\u0420\u0430\u0437\u0440\u0435\u0448\u0438\u0442\u044c \u0432\u0441\u0435\u0433\u0434\u0430") { submitApproval(permission, "approve_always") })
                add(actionButton("\u041e\u0442\u043a\u043b\u043e\u043d\u0438\u0442\u044c") { submitApproval(permission, "reject") })
            }, BorderLayout.EAST)
            approvalPanel.add(row)
        }
        approvalPanel.revalidate()
        approvalPanel.repaint()
    }

    private fun submitApproval(permission: ChatPendingPermissionDto, decision: String) {
        val active = sessionId ?: return
        ApplicationManager.getApplication().executeOnPooledThread {
            try {
                backendClient.submitChatToolDecision(
                    active,
                    ChatToolDecisionRequestDto(permissionId = permission.permissionId, decision = decision)
                )
                refreshControlPlaneAsync()
            } catch (ex: Exception) {
                logger.warn("Failed to submit decision", ex)
                SwingUtilities.invokeLater { appendSystemLine("\u041d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u043e\u0442\u043f\u0440\u0430\u0432\u0438\u0442\u044c \u0440\u0435\u0448\u0435\u043d\u0438\u0435: ${ex.message}") }
            }
        }
    }

    private fun startEventStreamAsync(activeSession: String) {
        if (streamSessionId == activeSession && streamCall != null) return
        stopEventStream()
        streamSessionId = activeSession
        setConnectionState(ConnectionState.CONNECTING)

        ApplicationManager.getApplication().executeOnPooledThread {
            val base = settings.backendUrl.trimEnd('/')
            val request = Request.Builder().url("$base/sessions/$activeSession/stream?fromIndex=$streamFromIndex").get().build()
            val call = streamClient.newCall(request)
            streamCall = call
            try {
                call.execute().use { response ->
                    if (!response.isSuccessful) {
                        setConnectionState(ConnectionState.RECONNECTING, "HTTP ${response.code}")
                        scheduleStreamReconnect(activeSession, "HTTP ${response.code}")
                        return@use
                    }
                    streamReconnectAttempt = 0
                    setConnectionState(ConnectionState.CONNECTED)
                    val source = response.body?.source() ?: run {
                        setConnectionState(ConnectionState.RECONNECTING, "\u041f\u0443\u0441\u0442\u043e\u0435 \u0442\u0435\u043b\u043e \u043e\u0442\u0432\u0435\u0442\u0430")
                        scheduleStreamReconnect(activeSession, "\u043f\u0443\u0441\u0442\u043e\u0439 \u043e\u0442\u0432\u0435\u0442")
                        return@use
                    }
                    var hasData = false
                    while (!source.exhausted() && isDisplayable && sessionId == activeSession) {
                        val line = source.readUtf8Line() ?: break
                        when {
                            line.startsWith("data:") -> {
                                hasData = true
                                updateStreamIndexFromLine(line)
                            }
                            line.isBlank() && hasData -> {
                                hasData = false
                                refreshControlPlaneAsync()
                            }
                        }
                    }
                }
            } catch (ex: Exception) {
                if (logger.isDebugEnabled) logger.debug("Stream disconnected", ex)
                setConnectionState(ConnectionState.RECONNECTING, ex.message ?: "\u041f\u043e\u0442\u043e\u043a \u043e\u0442\u043a\u043b\u044e\u0447\u0435\u043d")
                scheduleStreamReconnect(activeSession, ex.message ?: "РѕС‚РєР»СЋС‡РµРЅРѕ")
            } finally {
                if (streamCall == call) streamCall = null
            }
        }
    }

    private fun scheduleStreamReconnect(activeSession: String, reason: String) {
        if (!isDisplayable || sessionId != activeSession) return
        val exponent = minOf(streamReconnectAttempt, 5)
        val delayMs = minOf(30_000L, 1200L * (1L shl exponent))
        streamReconnectAttempt = minOf(streamReconnectAttempt + 1, 10)
        setConnectionState(ConnectionState.RECONNECTING, "\u041f\u0435\u0440\u0435\u043f\u043e\u0434\u043a\u043b\u044e\u0447\u0435\u043d\u0438\u0435 \u0447\u0435\u0440\u0435\u0437 ${delayMs}ms ($reason)")
        AppExecutorUtil.getAppScheduledExecutorService().schedule(
            {
                if (isDisplayable && sessionId == activeSession) {
                    startEventStreamAsync(activeSession)
                }
            },
            delayMs,
            TimeUnit.MILLISECONDS
        )
    }

    private fun stopEventStream() {
        streamCall?.cancel()
        streamCall = null
        streamSessionId = null
        setConnectionState(ConnectionState.OFFLINE)
    }

    private fun showHistoryScreen() {
        hideSlashPopup()
        cardLayout.show(bodyCards, "history")
    }

    private fun showChatScreen() {
        cardLayout.show(bodyCards, "chat")
    }

    private fun isGenerating(): Boolean = latestActivity in setOf("busy", "retry", "waiting_permission")

    private fun updateSendButtonState() {
        if (isGenerating()) {
            sendButton.text = ""
            sendButton.icon = AllIcons.Actions.Close
            sendButton.background = theme.stopButtonBackground
            sendButton.foreground = JBColor.WHITE
        } else {
            sendButton.text = ""
            sendButton.icon = AllIcons.Actions.Execute
            sendButton.background = theme.sendButtonBackground
            sendButton.foreground = JBColor.WHITE
        }
        sendButton.toolTipText = if (isGenerating()) "\u041e\u0441\u0442\u0430\u043d\u043e\u0432\u0438\u0442\u044c \u0433\u0435\u043d\u0435\u0440\u0430\u0446\u0438\u044e" else "\u041e\u0442\u043f\u0440\u0430\u0432\u0438\u0442\u044c \u0441\u043e\u043e\u0431\u0449\u0435\u043d\u0438\u0435"
        sendButton.isOpaque = true
    }

    private fun setConnectionState(state: ConnectionState, details: String? = null) {
        connectionState = state
        if (!details.isNullOrBlank()) {
            connectionDetails = details
        } else if (state == ConnectionState.CONNECTED || state == ConnectionState.OFFLINE) {
            connectionDetails = null
        }
        updateStatusLabel()
    }

    private fun updateStatusLabel() {
        val runtimeText = selectedRuntime.title
        val activityText = when (latestActivity) {
            "busy" -> "Р’ СЂР°Р±РѕС‚Рµ"
            "retry" -> "\u041f\u043e\u0432\u0442\u043e\u0440"
            "waiting_permission" -> "РћР¶РёРґР°РЅРёРµ РїРѕРґС‚РІРµСЂР¶РґРµРЅРёСЏ"
            "error" -> "\u041e\u0448\u0438\u0431\u043a\u0430"
            else -> "Р“РѕС‚РѕРІ"
        }
        val connectionText = when (connectionState) {
            ConnectionState.CONNECTING -> "РїРѕРґРєР»СЋС‡РµРЅРёРµ"
            ConnectionState.CONNECTED -> "РїРѕРґРєР»СЋС‡РµРЅРѕ"
            ConnectionState.RECONNECTING -> "РїРµСЂРµРїРѕРґРєР»СЋС‡РµРЅРёРµ"
            ConnectionState.OFFLINE -> "РЅРµ РІ СЃРµС‚Рё"
        }
        val details = connectionDetails?.takeIf { it.isNotBlank() }
        val text = if (details != null) {
            "$runtimeText | $activityText | $connectionText | $details"
        } else {
            "$runtimeText | $activityText | $connectionText"
        }
        val online = connectionState == ConnectionState.CONNECTED
        if (SwingUtilities.isEventDispatchThread()) {
            statusLabel.text = text
            statusBadge.update(connectionText.replaceFirstChar(Char::uppercase), online)
        } else {
            SwingUtilities.invokeLater {
                statusLabel.text = text
                statusBadge.update(connectionText.replaceFirstChar(Char::uppercase), online)
            }
        }
    }

    private fun updateStreamIndexFromLine(line: String) {
        val payload = line.removePrefix("data:").trim()
        val match = sseIndexPattern.find(payload) ?: return
        val parsed = match.groupValues.getOrNull(1)?.toIntOrNull() ?: return
        streamFromIndex = maxOf(streamFromIndex, parsed + 1)
    }

    private fun appendSystemLine(text: String) {
        val shouldStickToBottom = isUserNearBottom()
        timelineLines.add(
            UiLine(
                kind = UiLineKind.SYSTEM,
                text = text,
                createdAt = Instant.now(),
                stableKey = "local-system-${System.nanoTime()}",
                source = UiLineSource.LOCAL_SYSTEM
            )
        )
        renderTimeline()
        scrollToBottomIfNeeded(shouldStickToBottom)
    }

    private fun updateRunEventsIndexFromLine(line: String) {
        val payload = line.removePrefix("data:").trim()
        val match = sseIndexPattern.find(payload) ?: return
        val parsed = match.groupValues.getOrNull(1)?.toIntOrNull() ?: return
        runEventsFromIndex = maxOf(runEventsFromIndex, parsed + 1)
    }

    private fun configureRunArtifactsPanel() {
        runArtifactsList.background = theme.containerBackground
        runArtifactsList.foreground = theme.primaryText
        runArtifactsList.selectionBackground = theme.controlBackground
        runArtifactsList.selectionForeground = theme.primaryText
        runArtifactsList.visibleRowCount = 6
        runArtifactsList.cellRenderer = ArtifactRenderer()
        runArtifactsList.addListSelectionListener {
            if (!it.valueIsAdjusting) {
                renderSelectedArtifactPreview(runArtifactsList.selectedValue)
            }
        }

        artifactPreviewArea.background = theme.containerBackground
        artifactPreviewArea.font = artifactPreviewArea.font.deriveFont(12.5f)

        val header = JPanel(BorderLayout()).apply {
            isOpaque = false
            add(JBLabel("Artifacts / Logs").apply {
                foreground = theme.primaryText
                font = font.deriveFont(Font.BOLD, 12.5f)
            }, BorderLayout.WEST)
            add(actionButton("Refresh") {
                currentArtifactRunId?.let { refreshRunArtifactsAsync(it, force = true) }
            }, BorderLayout.EAST)
        }

        val previewPanel = JPanel(BorderLayout()).apply {
            isOpaque = false
            add(artifactPreviewLabel, BorderLayout.NORTH)
            add(JBScrollPane(artifactPreviewArea).apply {
                border = JBUI.Borders.compound(
                    BorderFactory.createLineBorder(theme.containerBorder, 1, true),
                    JBUI.Borders.empty(2)
                )
                viewport.background = theme.containerBackground
                background = theme.containerBackground
                preferredSize = Dimension(100, 150)
            }, BorderLayout.CENTER)
        }

        runArtifactsPanel.add(header, BorderLayout.NORTH)
        runArtifactsPanel.add(
            JSplitPane(JSplitPane.VERTICAL_SPLIT,
                JBScrollPane(runArtifactsList).apply {
                    border = JBUI.Borders.compound(
                        BorderFactory.createLineBorder(theme.containerBorder, 1, true),
                        JBUI.Borders.empty(2)
                    )
                    viewport.background = theme.containerBackground
                    background = theme.containerBackground
                    preferredSize = Dimension(100, 120)
                },
                previewPanel
            ).apply {
                border = JBUI.Borders.emptyTop(6)
                resizeWeight = 0.38
                isOpaque = false
            },
            BorderLayout.CENTER
        )
    }

    private fun configureRunEventsPanel() {
        runEventsList.background = theme.containerBackground
        runEventsList.foreground = theme.primaryText
        runEventsList.selectionBackground = theme.controlBackground
        runEventsList.selectionForeground = theme.primaryText
        runEventsList.visibleRowCount = 5
        runEventsList.cellRenderer = RunEventRenderer()
        runEventsList.addListSelectionListener {
            if (!it.valueIsAdjusting) {
                renderSelectedRunEvent(runEventsList.selectedValue)
            }
        }

        runEventPreviewArea.background = theme.containerBackground
        runEventPreviewArea.font = runEventPreviewArea.font.deriveFont(12.5f)

        val header = JPanel(BorderLayout()).apply {
            isOpaque = false
            add(JBLabel("Live Activity").apply {
                foreground = theme.primaryText
                font = font.deriveFont(Font.BOLD, 12.5f)
            }, BorderLayout.WEST)
        }

        val previewPanel = JPanel(BorderLayout()).apply {
            isOpaque = false
            add(runEventPreviewLabel, BorderLayout.NORTH)
            add(JBScrollPane(runEventPreviewArea).apply {
                border = JBUI.Borders.compound(
                    BorderFactory.createLineBorder(theme.containerBorder, 1, true),
                    JBUI.Borders.empty(2)
                )
                viewport.background = theme.containerBackground
                background = theme.containerBackground
                preferredSize = Dimension(100, 130)
            }, BorderLayout.CENTER)
        }

        runEventsPanel.add(header, BorderLayout.NORTH)
        runEventsPanel.add(
            JSplitPane(
                JSplitPane.VERTICAL_SPLIT,
                JBScrollPane(runEventsList).apply {
                    border = JBUI.Borders.compound(
                        BorderFactory.createLineBorder(theme.containerBorder, 1, true),
                        JBUI.Borders.empty(2)
                    )
                    viewport.background = theme.containerBackground
                    background = theme.containerBackground
                    preferredSize = Dimension(100, 110)
                },
                previewPanel
            ).apply {
                border = JBUI.Borders.emptyTop(6)
                resizeWeight = 0.42
                isOpaque = false
            },
            BorderLayout.CENTER
        )
    }

    private fun ensureRunEventStream(runId: String) {
        if (runEventsStreamRunId == runId && runEventsStreamCall != null) {
            return
        }
        stopRunEventStream()
        if (runEventsStreamRunId != runId) {
            clearRunEventsPanel()
            runEventsFromIndex = 0
        }
        runEventsStreamRunId = runId

        ApplicationManager.getApplication().executeOnPooledThread {
            val base = settings.backendUrl.trimEnd('/')
            val request = Request.Builder()
                .url("$base/runs/$runId/events?fromIndex=$runEventsFromIndex")
                .header("Accept", "text/event-stream")
                .get()
                .build()
            val call = streamClient.newCall(request)
            runEventsStreamCall = call
            try {
                call.execute().use { response ->
                    if (!response.isSuccessful) {
                        throw IllegalStateException("HTTP ${response.code}")
                    }
                    val source = response.body?.source() ?: return@use
                    var currentEventType: String? = null
                    var currentData = StringBuilder()
                    while (!source.exhausted() && isDisplayable && runEventsStreamRunId == runId) {
                        val line = source.readUtf8Line() ?: break
                        when {
                            line.startsWith("event:") -> {
                                currentEventType = line.removePrefix("event:").trim()
                            }
                            line.startsWith("data:") -> {
                                updateRunEventsIndexFromLine(line)
                                if (currentData.isNotEmpty()) {
                                    currentData.append('\n')
                                }
                                currentData.append(line.removePrefix("data:").trim())
                            }
                            line.isBlank() -> {
                                val eventType = currentEventType
                                val payload = currentData.toString().trim()
                                if (!eventType.isNullOrBlank() && payload.isNotBlank()) {
                                    handleRunEventData(runId, eventType, payload)
                                }
                                currentEventType = null
                                currentData = StringBuilder()
                            }
                        }
                    }
                }
            } catch (ex: Exception) {
                if (logger.isDebugEnabled) {
                    logger.debug("Run event stream disconnected for $runId", ex)
                }
            } finally {
                if (runEventsStreamCall == call) {
                    runEventsStreamCall = null
                }
            }
        }
    }

    private fun stopRunEventStream() {
        runEventsStreamCall?.cancel()
        runEventsStreamCall = null
        runEventsStreamRunId = null
        runEventsFromIndex = 0
    }

    private fun handleRunEventData(runId: String, eventType: String, rawData: String) {
        if (eventType.equals("heartbeat", ignoreCase = true)) {
            return
        }
        val event = try {
            jsonMapper.readValue<RunEventResponseDto>(rawData)
        } catch (ex: Exception) {
            if (logger.isDebugEnabled) {
                logger.debug("Failed to parse run event for $runId: $rawData", ex)
            }
            return
        }
        SwingUtilities.invokeLater {
            if (runEventsStreamRunId != runId && currentArtifactRunId != runId && lastAgentRunId != runId) {
                return@invokeLater
            }
            appendRunEvent(event)
        }
    }

    private fun appendRunEvent(event: RunEventResponseDto) {
        if ((0 until runEventsModel.size()).any { runEventsModel.get(it).index == event.index }) {
            return
        }
        val item = LiveRunEventItem(
            index = event.index,
            eventType = event.eventType,
            createdAt = event.createdAt,
            summary = summarizeRunEvent(event),
            details = buildRunEventDetails(event)
        )
        runEventsModel.addElement(item)
        while (runEventsModel.size() > 200) {
            runEventsModel.remove(0)
        }
        runEventsPanel.isVisible = true
        val previousSelection = selectedRunEventIndex
        if (previousSelection == null || previousSelection == item.index || runEventsModel.size() == 1) {
            runEventsList.selectedIndex = runEventsModel.size() - 1
        }
    }

    private fun clearRunEventsPanel() {
        selectedRunEventIndex = null
        runEventsModel.clear()
        runEventsList.clearSelection()
        runEventPreviewLabel.text = "Live Activity"
        runEventPreviewArea.text = UiStrings.runEventsPlaceholder
        runEventsPanel.isVisible = false
    }

    private fun renderSelectedRunEvent(item: LiveRunEventItem?) {
        if (item == null) {
            selectedRunEventIndex = null
            runEventPreviewLabel.text = "Live Activity"
            runEventPreviewArea.text = "Select an event to inspect it."
            return
        }
        selectedRunEventIndex = item.index
        runEventPreviewLabel.text = "${item.eventType} | ${timeFormatter.format(item.createdAt)}"
        runEventPreviewArea.text = item.details
        runEventPreviewArea.caretPosition = 0
    }

    private fun refreshRunArtifactsAsync(runId: String, force: Boolean = false) {
        val now = System.currentTimeMillis()
        if (!force && currentArtifactRunId == runId && now - lastArtifactsRefreshAtMs < 1_500L) {
            return
        }
        if (!artifactsRefreshInFlight.compareAndSet(false, true)) {
            return
        }
        currentArtifactRunId = runId
        ApplicationManager.getApplication().executeOnPooledThread {
            try {
                val response = backendClient.listRunArtifacts(runId)
                SwingUtilities.invokeLater {
                    lastArtifactsRefreshAtMs = System.currentTimeMillis()
                    applyRunArtifacts(runId, response.items)
                }
            } catch (ex: Exception) {
                logger.debug("Failed to refresh run artifacts for $runId", ex)
                SwingUtilities.invokeLater {
                    artifactPreviewLabel.text = "Artifacts"
                    artifactPreviewArea.text = "Failed to load artifacts: ${ex.message}"
                    runArtifactsPanel.isVisible = true
                }
            } finally {
                artifactsRefreshInFlight.set(false)
            }
        }
    }

    private fun applyRunArtifacts(runId: String, items: List<RunArtifactDto>) {
        currentArtifactRunId = runId
        val sortedItems = items.sortedWith(
            compareByDescending<RunArtifactDto> { isLikelyLogArtifact(it) }
                .thenBy { it.attemptId ?: "" }
                .thenBy { it.name.lowercase(Locale.getDefault()) }
        )
        val desiredKey = selectedArtifactKey
        runArtifactsModel.clear()
        sortedItems.forEach(runArtifactsModel::addElement)
        runArtifactsPanel.isVisible = true

        if (sortedItems.isEmpty()) {
            artifactPreviewLabel.text = "Artifacts"
            artifactPreviewArea.text = "No artifacts published for this run yet."
            selectedArtifactKey = null
            runArtifactsList.clearSelection()
            return
        }

        val preferredIndex = sortedItems.indexOfFirst { artifactKey(it) == desiredKey }
            .takeIf { it >= 0 }
            ?: sortedItems.indexOfFirst(::isLikelyLogArtifact).takeIf { it >= 0 }
            ?: 0
        runArtifactsList.selectedIndex = preferredIndex
        runArtifactsList.ensureIndexIsVisible(preferredIndex)
    }

    private fun clearRunArtifactsPanel() {
        currentArtifactRunId = null
        selectedArtifactKey = null
        lastAgentRunId = null
        lastArtifactsRefreshAtMs = 0L
        runArtifactsModel.clear()
        runArtifactsList.clearSelection()
        artifactPreviewLabel.text = "Artifacts"
        artifactPreviewArea.text = UiStrings.runArtifactsPlaceholder
        runArtifactsPanel.isVisible = false
    }

    private fun renderSelectedArtifactPreview(artifact: RunArtifactDto?) {
        if (artifact == null) {
            artifactPreviewLabel.text = "Artifacts"
            artifactPreviewArea.text = "Select an artifact to inspect it."
            return
        }
        selectedArtifactKey = artifactKey(artifact)
        artifactPreviewLabel.text = buildArtifactCaption(artifact)
        val inlineContent = artifact.content?.takeIf { it.isNotBlank() }
        if (inlineContent != null) {
            artifactPreviewArea.text = trimPreview(inlineContent)
            artifactPreviewArea.caretPosition = 0
            return
        }
        if (!isPreviewableTextArtifact(artifact)) {
            artifactPreviewArea.text = buildNonTextArtifactSummary(artifact)
            artifactPreviewArea.caretPosition = 0
            return
        }
        val runId = currentArtifactRunId ?: return
        artifactPreviewArea.text = "Loading ${artifact.name}..."
        ApplicationManager.getApplication().executeOnPooledThread {
            try {
                val artifactId = artifact.artifactId
                    ?: throw IllegalStateException("Artifact content is unavailable for ${artifact.name}")
                val content = backendClient.getRunArtifactContent(runId, artifactId)
                SwingUtilities.invokeLater {
                    if (currentArtifactRunId == runId && selectedArtifactKey == artifactKey(artifact)) {
                        artifactPreviewArea.text = trimPreview(content)
                        artifactPreviewArea.caretPosition = 0
                    }
                }
            } catch (ex: Exception) {
                SwingUtilities.invokeLater {
                    if (currentArtifactRunId == runId && selectedArtifactKey == artifactKey(artifact)) {
                        artifactPreviewArea.text = buildNonTextArtifactSummary(artifact) + "\n\nPreview unavailable: ${ex.message}"
                        artifactPreviewArea.caretPosition = 0
                    }
                }
            }
        }
    }

    private fun artifactKey(artifact: RunArtifactDto): String =
        listOfNotNull(artifact.artifactId, artifact.uri.ifBlank { null }, artifact.name).joinToString("|")

    private fun buildArtifactCaption(artifact: RunArtifactDto): String {
        val type = artifact.mediaType ?: "unknown"
        val attempt = artifact.attemptId?.let { " | attempt ${it.take(8)}" }.orEmpty()
        return "${artifact.name} | $type$attempt"
    }

    private fun buildNonTextArtifactSummary(artifact: RunArtifactDto): String {
        val lines = mutableListOf<String>()
        lines += "name: ${artifact.name}"
        lines += "type: ${artifact.mediaType ?: "unknown"}"
        artifact.size?.let { lines += "size: $it bytes" }
        artifact.attemptId?.let { lines += "attempt: $it" }
        artifact.connectorSource?.let { lines += "source: $it" }
        artifact.signedUrl?.takeIf { it.isNotBlank() }?.let { lines += "url: $it" }
        if (artifact.uri.isNotBlank()) {
            lines += "uri: ${artifact.uri}"
        }
        return lines.joinToString("\n")
    }

    private fun trimPreview(content: String): String =
        StringUtil.shortenTextWithEllipsis(content.trim(), 12_000, 0, true)

    private fun isPreviewableTextArtifact(artifact: RunArtifactDto): Boolean {
        val mediaType = artifact.mediaType?.lowercase(Locale.getDefault()).orEmpty()
        if (mediaType.startsWith("text/")) {
            return true
        }
        if (mediaType.contains("json") || mediaType.contains("xml") || mediaType.contains("yaml")) {
            return true
        }
        val lowerName = artifact.name.lowercase(Locale.getDefault())
        return listOf(".log", ".txt", ".md", ".json", ".xml", ".yml", ".yaml", ".diff", ".patch")
            .any(lowerName::endsWith)
    }

    private fun isLikelyLogArtifact(artifact: RunArtifactDto): Boolean {
        val lowerName = artifact.name.lowercase(Locale.getDefault())
        return lowerName.contains("log") || lowerName.endsWith(".log") || lowerName.endsWith(".txt")
    }

    private fun summarizeRunEvent(event: RunEventResponseDto): String {
        val payload = event.payload
        return when (event.eventType) {
            "run.started" -> "Run started"
            "run.awaiting_approval" -> {
                val title = payload["title"]?.toString()
                    ?: (payload["approval"] as? Map<*, *>)?.get("title")?.toString()
                    ?: "Approval required"
                "Approval required: $title"
            }
            "run.artifact_published" -> {
                val artifactName = (payload["artifact"] as? Map<*, *>)?.get("name")?.toString() ?: "artifact"
                "Artifact published: $artifactName"
            }
            "run.finished" -> "Run finished"
            "run.failed" -> "Run failed: ${payload["message"]?.toString() ?: "unknown error"}"
            "run.cancelled" -> "Run cancelled"
            "approval.decision" -> "Approval decision: ${payload["decision"]?.toString() ?: "sent"}"
            else -> {
                payload["message"]?.toString()
                    ?: payload["currentAction"]?.toString()
                    ?: extractNestedProgressText(payload)
                    ?: event.eventType
            }
        }
    }

    private fun extractNestedProgressText(payload: Map<String, Any?>): String? {
        val nestedPayload = payload["payload"] as? Map<*, *> ?: return null
        val properties = nestedPayload["properties"] as? Map<*, *>
        val info = properties?.get("info") as? Map<*, *>
        val part = properties?.get("part") as? Map<*, *>
        return listOfNotNull(
            part?.get("type")?.toString()?.takeIf { it.isNotBlank() }?.let { "part: $it" },
            info?.get("error")?.toString()?.takeIf { it.isNotBlank() },
            properties?.get("permission")?.toString()?.takeIf { it.isNotBlank() },
            nestedPayload["type"]?.toString()?.takeIf { it.isNotBlank() }
        ).firstOrNull()
    }

    private fun buildRunEventDetails(event: RunEventResponseDto): String {
        val prettyPayload = try {
            jsonMapper.writerWithDefaultPrettyPrinter().writeValueAsString(event.payload)
        } catch (_: Exception) {
            event.payload.toString()
        }
        return buildString {
            append("summary: ")
            append(summarizeRunEvent(event))
            append("\n")
            append("type: ")
            append(event.eventType)
            append("\n")
            append("index: ")
            append(event.index)
            append("\n")
            append("time: ")
            append(event.createdAt)
            append("\n\npayload:\n")
            append(prettyPayload)
        }
    }

    private fun upsertProgressLine(text: String) {
        val shouldStickToBottom = isUserNearBottom()
        val idx = timelineLines.indexOfFirst { it.source == UiLineSource.PROGRESS }.takeIf { it >= 0 }
        if (idx == null) {
            timelineLines.add(
                UiLine(
                    kind = UiLineKind.PROGRESS,
                    text = text,
                    createdAt = Instant.now(),
                    stableKey = "progress",
                    source = UiLineSource.PROGRESS
                )
            )
        } else {
            val existing = timelineLines[idx]
            if (existing.text != text) {
                timelineLines[idx] = existing.copy(text = text, createdAt = Instant.now())
            }
        }
        renderTimeline()
        scrollToBottomIfNeeded(shouldStickToBottom)
    }

    private fun removeProgressLine() {
        val idx = timelineLines.indexOfFirst { it.source == UiLineSource.PROGRESS }
        if (idx < 0) return
        timelineLines.removeAt(idx)
        renderTimeline()
    }

    private fun replaceTimelineModelIncrementally(target: List<UiLine>) {
        if (timelineLines == target) return
        timelineLines.clear()
        timelineLines.addAll(target)
        renderTimeline()
    }

    private fun isUserNearBottom(): Boolean {
        val scrollPane = timelineScrollPane ?: return true
        val viewport = scrollPane.viewport ?: return true
        val view = viewport.view ?: return true
        val viewHeight = view.preferredSize.height
        if (viewHeight <= 0) return true
        val bottomY = viewport.viewPosition.y + viewport.extentSize.height
        return bottomY >= viewHeight - autoScrollBottomThresholdPx
    }

    private fun scrollToBottomIfNeeded(shouldStickToBottom: Boolean) {
        if (!shouldStickToBottom) return
        SwingUtilities.invokeLater {
            val scrollBar = timelineScrollPane?.verticalScrollBar ?: return@invokeLater
            scrollBar.value = scrollBar.maximum - scrollBar.visibleAmount
        }
    }

    private fun maybeShowSlashPopup() {
        if (isApplyingSlashSelection) {
            isApplyingSlashSelection = false
            return
        }

        val value = inputArea.text.trim()
        if (value.isBlank() || !value.startsWith("/")) {
            suppressSlashPopupUntilReset = false
            lastSlashMatches = emptyList()
            hideSlashPopup()
            return
        }
        val token = value.removePrefix("/").lowercase()
        if (token.contains(" ")) {
            hideSlashPopup()
            return
        }
        if (suppressSlashPopupUntilReset) {
            hideSlashPopup()
            return
        }

        val matches = slashTemplates
            .filter { it.key.startsWith(token) || it.title.lowercase().contains(token) }
            .map { "/${it.key} - ${it.title}" }
        if (matches.isEmpty()) {
            lastSlashMatches = emptyList()
            hideSlashPopup()
            return
        }
        if (matches == lastSlashMatches && slashPopup != null) {
            return
        }

        hideSlashPopup()
        val step = object : BaseListPopupStep<String>("РЁР°Р±Р»РѕРЅС‹", matches) {
            override fun onChosen(selectedValue: String?, finalChoice: Boolean): PopupStep<*> {
                if (selectedValue != null) {
                    val selectedKey = selectedValue.removePrefix("/").substringBefore(" ").trim()
                    val templateText = slashTemplates.firstOrNull { it.key == selectedKey }?.text ?: ""
                    isApplyingSlashSelection = true
                    suppressSlashPopupUntilReset = true
                    inputArea.text = templateText
                    inputArea.caretPosition = inputArea.text.length
                    hideSlashPopup()
                }
                return FINAL_CHOICE
            }
        }
        val popup = JBPopupFactory.getInstance().createListPopup(step)
        popup.addListener(object : JBPopupListener {
            override fun onClosed(event: LightweightWindowEvent) {
                if (slashPopup == popup) slashPopup = null
            }
        })
        lastSlashMatches = matches
        slashPopup = popup
        popup.show(RelativePoint.getSouthWestOf(inputArea))
    }

    private fun hideSlashPopup() {
        slashPopup?.cancel()
        slashPopup = null
    }

    private fun actionButton(text: String, action: () -> Unit): JButton = JButton(text).apply {
        foreground = theme.primaryText
        background = theme.controlBackground
        border = BorderFactory.createLineBorder(theme.controlBorder, 1, true)
        isContentAreaFilled = true
        isFocusPainted = false
        cursor = Cursor.getPredefinedCursor(Cursor.HAND_CURSOR)
        addActionListener { action() }
    }

    private data class UiLine(
        val kind: UiLineKind,
        val text: String,
        val createdAt: Instant,
        val stableKey: String,
        val source: UiLineSource
    )

    private data class SlashTemplateItem(
        val key: String,
        val title: String,
        val text: String
    )

    private data class LiveRunEventItem(
        val index: Int,
        val eventType: String,
        val createdAt: Instant,
        val summary: String,
        val details: String
    )

    private enum class RuntimeMode(
        val backendValue: String,
        val title: String,
        val defaultProfile: String
    ) {
        CHAT("chat", "Chat", "quick"),
        AGENT("opencode", "Agent", "agent")
    }

    private enum class UiLineSource {
        SERVER_MESSAGE,
        LOCAL_SYSTEM,
        PROGRESS
    }

    private enum class ConnectionState {
        CONNECTING,
        CONNECTED,
        RECONNECTING,
        OFFLINE
    }

    private enum class UiLineKind {
        USER,
        ASSISTANT,
        SYSTEM,
        PROGRESS
    }

    private fun runtimeModeFromBackend(value: String?): RuntimeMode =
        RuntimeMode.values().firstOrNull { it.backendValue.equals(value ?: "chat", ignoreCase = true) } ?: RuntimeMode.CHAT

    private fun uiThemeColor(keys: List<String>, fallback: Color): JBColor {
        val resolved = keys.asSequence().mapNotNull { UIManager.getColor(it) }.firstOrNull() ?: fallback
        return JBColor(resolved, resolved)
    }

    private fun renderTimeline() {
        timelineContainer.removeAll()
        if (timelineLines.isEmpty()) {
            timelineContainer.add(
                JBLabel("\u0417\u0430\u0434\u0430\u0439\u0442\u0435 \u0432\u043e\u043f\u0440\u043e\u0441 \u043f\u043e \u043f\u0440\u043e\u0435\u043a\u0442\u0443").apply {
                    foreground = theme.secondaryText
                    border = JBUI.Borders.empty(12, 10, 8, 10)
                }
            )
        } else {
            timelineLines.forEach { line -> timelineContainer.add(buildTimelineLine(line)) }
        }
        timelineContainer.revalidate()
        timelineContainer.repaint()
    }

    private fun buildTimelineLine(line: UiLine): JPanel {
        val viewportWidth = timelineScrollPane?.viewport?.extentSize?.width ?: width
        val contentWidth = maxOf(140, viewportWidth - 56)
        val textArea = JBTextArea(line.text).apply {
            isEditable = false
            isFocusable = true
            isOpaque = false
            lineWrap = true
            wrapStyleWord = true
            font = font.deriveFont(13.5f)
            foreground = if (line.kind == UiLineKind.SYSTEM) theme.systemText else theme.primaryText
            border = JBUI.Borders.empty(8, 11)
            setSize(Dimension(contentWidth, Int.MAX_VALUE))
            preferredSize = Dimension(contentWidth, preferredSize.height)
            maximumSize = Dimension(contentWidth, Int.MAX_VALUE)
        }

        val row = JPanel(BorderLayout()).apply {
            isOpaque = false
            border = JBUI.Borders.empty(4, 8, 4, 8)
        }

        when (line.kind) {
            UiLineKind.USER -> {
                val bubble = JPanel(BorderLayout()).apply {
                    isOpaque = true
                    background = theme.userBubble
                    border = JBUI.Borders.compound(
                        RoundedLineBorder(theme.userBubbleBorder, 1, 18),
                        JBUI.Borders.empty()
                    )
                    add(textArea, BorderLayout.CENTER)
                    maximumSize = Dimension(contentWidth, Int.MAX_VALUE)
                }
                row.add(bubble, BorderLayout.CENTER)
            }
            UiLineKind.ASSISTANT -> {
                row.add(textArea, BorderLayout.CENTER)
            }
            UiLineKind.PROGRESS -> {
                val bubble = JPanel(BorderLayout()).apply {
                    isOpaque = true
                    background = theme.progressBubble
                    border = JBUI.Borders.compound(
                        RoundedLineBorder(theme.containerBorder, 1, 14),
                        JBUI.Borders.empty()
                    )
                    add(textArea, BorderLayout.CENTER)
                    maximumSize = Dimension(contentWidth, Int.MAX_VALUE)
                }
                row.add(bubble, BorderLayout.WEST)
            }
            UiLineKind.SYSTEM -> {
                row.add(textArea, BorderLayout.WEST)
            }
        }
        return row
    }

    private inner class UiTheme {
        val panelBackground: JBColor = uiThemeColor(listOf("Panel.background"), Color(0x2E, 0x32, 0x39))
        val containerBackground: JBColor = uiThemeColor(listOf("TextArea.background", "Panel.background"), Color(0x35, 0x39, 0x41))
        val inputBackground: JBColor = uiThemeColor(listOf("TextField.background", "TextArea.background"), Color(0x3A, 0x3F, 0x47))
        val controlBackground: JBColor = uiThemeColor(listOf("Button.background", "Panel.background"), Color(0x3C, 0x41, 0x49))
        val containerBorder: JBColor = uiThemeColor(listOf("Component.borderColor", "Borders.color"), Color(0x4A, 0x50, 0x5A))
        val controlBorder: JBColor = uiThemeColor(listOf("Component.borderColor", "Borders.color"), Color(0x52, 0x59, 0x64))
        val primaryText: JBColor = uiThemeColor(listOf("Label.foreground"), Color(0xE7, 0xEA, 0xEF))
        val secondaryText: JBColor = uiThemeColor(listOf("Label.disabledForeground", "Component.infoForeground"), Color(0x9A, 0xA1, 0xAD))
        val systemText: JBColor = uiThemeColor(listOf("Component.errorFocusColor", "ValidationTooltip.errorForeground"), Color(0xD8, 0x5D, 0x5D))
        val sendButtonBackground: JBColor = uiThemeColor(listOf("Button.default.background"), Color(0x66, 0x7A, 0x9B))
        val stopButtonBackground: JBColor = uiThemeColor(listOf("Actions.Red"), Color(0xC2, 0x4A, 0x4A))
        val userBubble: JBColor = uiThemeColor(listOf("EditorPane.background", "TextArea.background"), Color(0x56, 0x5D, 0x69))
        val userBubbleBorder: JBColor = uiThemeColor(listOf("Component.borderColor", "Borders.color"), Color(0x63, 0x6B, 0x77))
        val progressBubble: JBColor = uiThemeColor(listOf("ToolTip.background", "Panel.background"), Color(0x4E, 0x55, 0x61))
    }

    private class RoundedLineBorder(
        private val color: Color,
        private val strokeWidth: Int,
        private val arc: Int
    ) : AbstractBorder() {
        override fun getBorderInsets(c: Component?): Insets = Insets(1, 1, 1, 1)

        override fun paintBorder(c: Component?, g: Graphics, x: Int, y: Int, width: Int, height: Int) {
            val g2 = g.create() as Graphics2D
            g2.setRenderingHint(RenderingHints.KEY_ANTIALIASING, RenderingHints.VALUE_ANTIALIAS_ON)
            g2.color = color
            g2.stroke = java.awt.BasicStroke(strokeWidth.toFloat())
            g2.drawRoundRect(x, y, width - strokeWidth, height - strokeWidth, arc, arc)
            g2.dispose()
        }
    }

    private inner class SessionRenderer(private val formatter: DateTimeFormatter) : DefaultListCellRenderer() {
        override fun getListCellRendererComponent(
            list: JList<*>,
            value: Any?,
            index: Int,
            isSelected: Boolean,
            cellHasFocus: Boolean
        ): Component {
            val item = value as? ChatSessionListItemDto
            val text = if (item == null) {
                ""
            } else {
                val preview = item.lastMessagePreview?.takeIf { it.isNotBlank() } ?: "\u0421\u0435\u0441\u0441\u0438\u044f ${item.sessionId.take(8)}"
                "[${runtimeModeFromBackend(item.runtime).title}] $preview  |  ${formatter.format(item.updatedAt)}  |  ${item.activity}"
            }
            return (super.getListCellRendererComponent(list, text, index, isSelected, cellHasFocus) as DefaultListCellRenderer).apply {
                border = JBUI.Borders.empty(8, 10)
                foreground = if (isSelected) theme.primaryText else theme.primaryText
                background = if (isSelected) theme.controlBackground else theme.containerBackground
            }
        }
    }

    private inner class ArtifactRenderer : DefaultListCellRenderer() {
        override fun getListCellRendererComponent(
            list: JList<*>,
            value: Any?,
            index: Int,
            isSelected: Boolean,
            cellHasFocus: Boolean
        ): Component {
            val item = value as? RunArtifactDto
            val typeHint = item?.mediaType?.substringBefore(';')?.takeIf { it.isNotBlank() } ?: "artifact"
            val text = if (item == null) {
                ""
            } else {
                "${item.name}  |  $typeHint"
            }
            return (super.getListCellRendererComponent(list, text, index, isSelected, cellHasFocus) as DefaultListCellRenderer).apply {
                border = JBUI.Borders.empty(6, 8)
                foreground = theme.primaryText
                background = if (isSelected) theme.controlBackground else theme.containerBackground
            }
        }
    }

    private inner class RunEventRenderer : DefaultListCellRenderer() {
        override fun getListCellRendererComponent(
            list: JList<*>,
            value: Any?,
            index: Int,
            isSelected: Boolean,
            cellHasFocus: Boolean
        ): Component {
            val item = value as? LiveRunEventItem
            val text = if (item == null) {
                ""
            } else {
                "${timeFormatter.format(item.createdAt)}  |  ${item.summary}"
            }
            return (super.getListCellRendererComponent(list, text, index, isSelected, cellHasFocus) as DefaultListCellRenderer).apply {
                border = JBUI.Borders.empty(6, 8)
                foreground = theme.primaryText
                background = if (isSelected) theme.controlBackground else theme.containerBackground
            }
        }
    }

    private inner class RuntimeModeRenderer : DefaultListCellRenderer() {
        override fun getListCellRendererComponent(
            list: JList<*>,
            value: Any?,
            index: Int,
            isSelected: Boolean,
            cellHasFocus: Boolean
        ): Component {
            val mode = value as? RuntimeMode
            return (super.getListCellRendererComponent(list, mode?.title ?: "", index, isSelected, cellHasFocus) as DefaultListCellRenderer).apply {
                foreground = theme.primaryText
                background = if (isSelected) theme.controlBackground else theme.containerBackground
                border = JBUI.Borders.empty(4, 8)
            }
        }
    }
}






