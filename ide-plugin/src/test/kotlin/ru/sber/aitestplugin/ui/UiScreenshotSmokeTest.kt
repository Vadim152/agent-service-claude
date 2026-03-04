package ru.sber.aitestplugin.ui

import com.intellij.ui.components.JBCheckBox
import com.intellij.ui.components.JBList
import com.intellij.ui.components.JBTextArea
import com.intellij.ui.components.JBTextField
import org.junit.Assert.assertTrue
import org.junit.Test
import ru.sber.aitestplugin.model.GenerationRuleActionsDto
import ru.sber.aitestplugin.model.GenerationRuleConditionDto
import ru.sber.aitestplugin.model.GenerationRuleDto
import ru.sber.aitestplugin.model.StepTemplateDto
import ru.sber.aitestplugin.ui.components.EmptyStatePanel
import ru.sber.aitestplugin.ui.components.SectionCard
import ru.sber.aitestplugin.ui.components.StatusBadge
import ru.sber.aitestplugin.ui.dialogs.MemoryManagerContentPanel
import ru.sber.aitestplugin.ui.dialogs.buildApplyFeatureFormPanel
import ru.sber.aitestplugin.ui.dialogs.buildGenerateFeatureFormPanel
import ru.sber.aitestplugin.ui.theme.PluginUiTheme
import ru.sber.aitestplugin.ui.toolwindow.components.ChatComposerPanel
import ru.sber.aitestplugin.ui.toolwindow.components.RunDetailsPanel
import ru.sber.aitestplugin.ui.toolwindow.components.ToolWindowHeaderPanel
import java.awt.BorderLayout
import java.awt.Color
import java.awt.Dimension
import java.awt.FlowLayout
import java.awt.image.BufferedImage
import java.io.File
import java.io.FileInputStream
import java.io.FileOutputStream
import java.security.MessageDigest
import java.util.Properties
import javax.imageio.ImageIO
import javax.swing.DefaultListModel
import javax.swing.JButton
import javax.swing.JComboBox
import javax.swing.JComponent
import javax.swing.JLabel
import javax.swing.JPanel
import javax.swing.JScrollPane
import javax.swing.SwingUtilities

class UiScreenshotSmokeTest {
    private val baselineFile = File("src/test/resources/ui-smoke/baseline.properties")
    private val updateBaseline = java.lang.Boolean.getBoolean("ui.smoke.updateBaseline")
    private val sizeToleranceRatio = 0.2 // 20% delta allowed to avoid flaky PNG compression differences.

    @Test
    fun `tool window shell renders screenshot`() {
        val root = JPanel(BorderLayout(0, 12)).apply {
            background = PluginUiTheme.panelBackground
            isOpaque = true
            border = com.intellij.util.ui.JBUI.Borders.empty(12)
        }
        root.add(
            ToolWindowHeaderPanel(
                title = UiStrings.pluginName,
                statusComponent = StatusBadge("В сети", true),
                onNewSession = {},
                onShowHistory = {},
                onOpenSettings = {}
            ),
            BorderLayout.NORTH
        )

        val timelineCard = SectionCard(
            title = "Диалог",
            comment = "Поток сообщений, шагов агента и промежуточных обновлений.",
            content = JPanel(BorderLayout()).apply {
                isOpaque = false
                add(EmptyStatePanel("Пока нет сообщений", "Отправьте запрос, чтобы начать новую сессию."), BorderLayout.CENTER)
            }
        )

        val runInfo = JBTextArea("Run #42\nСтатус: waiting\nRuntime: chat").apply {
            isEditable = false
            lineWrap = true
            wrapStyleWord = true
            background = PluginUiTheme.inputBackground
            foreground = PluginUiTheme.primaryText
        }
        val events = JBTextArea("[12:40] session created\n[12:41] waiting for input").apply {
            isEditable = false
            lineWrap = true
            wrapStyleWord = true
            background = PluginUiTheme.inputBackground
            foreground = PluginUiTheme.primaryText
        }
        val artifacts = JBTextArea("generated.feature\nlogs/run-42.txt").apply {
            isEditable = false
            lineWrap = true
            wrapStyleWord = true
            background = PluginUiTheme.inputBackground
            foreground = PluginUiTheme.primaryText
        }
        val approvals = JPanel(FlowLayout(FlowLayout.LEFT, 8, 0)).apply {
            isOpaque = false
            add(JButton("Подтвердить"))
            add(JButton("Отклонить"))
        }
        val details = RunDetailsPanel(
            infoPanel = JScrollPane(runInfo),
            eventsPanel = JScrollPane(events),
            artifactsPanel = JScrollPane(artifacts),
            approvalsPanel = approvals
        )

        root.add(JPanel(BorderLayout(0, 12)).apply {
            isOpaque = false
            add(timelineCard, BorderLayout.CENTER)
            add(details, BorderLayout.SOUTH)
        }, BorderLayout.CENTER)

        root.add(
            ChatComposerPanel(
                runtimeSelector = JComboBox(arrayOf("chat", "run")),
                inputArea = JBTextArea("Сгенерируй feature по тест-кейсу из Jira").apply {
                    lineWrap = true
                    wrapStyleWord = true
                    background = PluginUiTheme.inputBackground
                    foreground = PluginUiTheme.primaryText
                },
                sendButton = JButton("Отправить"),
                statusComponent = JLabel("Ожидание")
            ),
            BorderLayout.SOUTH
        )

        val screenshot = renderComponent("toolwindow-shell", root, 1080, 760)
        assertTrue(screenshot.exists())
    }

    @Test
    fun `feature dialog forms render screenshots`() {
        val applyPanel = buildApplyFeatureFormPanel(
            targetPathField = JBTextField("src/test/resources/features/generated.feature"),
            createFileCheckbox = JBCheckBox(UiStrings.dialogCreateFile, true),
            overwriteCheckbox = JBCheckBox(UiStrings.dialogOverwriteFile, false)
        )
        val generatePanel = buildGenerateFeatureFormPanel(
            targetPathField = JBTextField("src/test/resources/features/generated.feature"),
            createFileCheckbox = JBCheckBox(UiStrings.dialogCreateFile, true),
            overwriteCheckbox = JBCheckBox(UiStrings.dialogOverwriteFile, true),
            memoryStatusLabel = JLabel("Preview loaded"),
            memoryPreviewArea = JBTextArea("Совпавших правил: 1\nСовпавших шаблонов: 2\nШагов для вставки: 3").apply {
                isEditable = false
                lineWrap = true
                wrapStyleWord = true
                background = PluginUiTheme.inputBackground
                foreground = PluginUiTheme.primaryText
            },
            refreshPreviewButton = JButton(UiStrings.dialogRefreshPreview)
        )

        val applyScreenshot = renderComponent("apply-feature-dialog", wrapForScreenshot(applyPanel), 720, 240)
        val generateScreenshot = renderComponent("generate-feature-dialog", wrapForScreenshot(generatePanel), 820, 480)

        assertTrue(applyScreenshot.exists())
        assertTrue(generateScreenshot.exists())
    }

    @Test
    fun `memory manager renders screenshot`() {
        val templatesModel = DefaultListModel<StepTemplateDto>().apply {
            addElement(
                StepTemplateDto(
                    id = "template-1",
                    name = "Логин",
                    triggerRegex = "login",
                    steps = listOf("Открыть страницу логина", "Ввести логин и пароль", "Нажать Войти")
                )
            )
            addElement(
                StepTemplateDto(
                    id = "template-2",
                    name = "Поиск",
                    steps = listOf("Открыть поиск", "Ввести запрос")
                )
            )
        }
        val rulesModel = DefaultListModel<GenerationRuleDto>().apply {
            addElement(
                GenerationRuleDto(
                    id = "rule-1",
                    name = "UI smoke",
                    condition = GenerationRuleConditionDto(textRegex = "smoke"),
                    actions = GenerationRuleActionsDto(language = "ru", applyTemplates = listOf("template-1"))
                )
            )
        }

        val rulesList = JBList(rulesModel).apply { selectedIndex = 0 }
        val templatesList = JBList(templatesModel).apply { selectedIndex = 0 }
        val rulesDetails = createReadOnlyArea("Правило: UI smoke\nActions: apply template Логин")
        val templatesDetails = createReadOnlyArea("Шаблон: Логин\n1. Открыть страницу логина")
        val rulesStatus = createReadOnlyArea("Правила для корня проекта:\nC:/repo\n\nЗагружено правил: 1.")
        val templatesStatus = createReadOnlyArea("Шаблоны для корня проекта:\nC:/repo\n\nЗагружено шаблонов: 2.")

        val panel = MemoryManagerContentPanel(
            projectRoot = "C:/repo",
            rulesList = rulesList,
            templatesList = templatesList,
            rulesDetailsArea = rulesDetails,
            templatesDetailsArea = templatesDetails,
            rulesStatusArea = rulesStatus,
            templatesStatusArea = templatesStatus,
            onRefreshRules = {},
            onAddRule = {},
            onEditRule = {},
            onDeleteRule = {},
            onRefreshTemplates = {},
            onAddTemplate = {},
            onEditTemplate = {},
            onDeleteTemplate = {},
        )

        val screenshot = renderComponent("memory-manager-dialog", panel, 1180, 760)
        assertTrue(screenshot.exists())
    }

    private fun wrapForScreenshot(component: JComponent): JComponent = JPanel(BorderLayout()).apply {
        background = PluginUiTheme.panelBackground
        isOpaque = true
        border = com.intellij.util.ui.JBUI.Borders.empty(16)
        add(component, BorderLayout.CENTER)
    }

    private fun createReadOnlyArea(text: String): JBTextArea = JBTextArea(text).apply {
        isEditable = false
        lineWrap = true
        wrapStyleWord = true
        background = PluginUiTheme.inputBackground
        foreground = PluginUiTheme.primaryText
    }

    private fun renderComponent(name: String, component: JComponent, width: Int, height: Int): File {
        val outputDir = File("build/reports/ui-smoke").apply { mkdirs() }
        val outputFile = File(outputDir, "$name.png")

        SwingUtilities.invokeAndWait {
            component.preferredSize = Dimension(width, height)
            component.size = Dimension(width, height)
            component.doLayout()
            component.validate()

            val image = BufferedImage(width, height, BufferedImage.TYPE_INT_ARGB)
            val graphics = image.createGraphics()
            try {
                graphics.color = component.background ?: Color.WHITE
                graphics.fillRect(0, 0, width, height)
                component.printAll(graphics)
            } finally {
                graphics.dispose()
            }
            ImageIO.write(image, "png", outputFile)
            assertHasVisualContent(image, outputFile)
        }

        return outputFile
    }

    private fun assertHasVisualContent(image: BufferedImage, outputFile: File) {
        assertTrue("Screenshot file is unexpectedly small: ${outputFile.length()} bytes", outputFile.length() > 900)
        assertTrue("Screenshot dimensions are invalid", image.width > 200 && image.height > 150)
        assertSnapshotBaseline(outputFile.nameWithoutExtension, image, outputFile)
    }

    private fun assertSnapshotBaseline(name: String, image: BufferedImage, outputFile: File) {
        val props = loadBaselineProperties()
        val hash = sha256(outputFile)
        val size = outputFile.length()

        if (updateBaseline) {
            props["$name.width"] = image.width.toString()
            props["$name.height"] = image.height.toString()
            props["$name.size"] = size.toString()
            props["$name.sha256"] = hash
            saveBaselineProperties(props)
            return
        }

        val baselineWidth = props.getProperty("$name.width")?.toIntOrNull()
        val baselineHeight = props.getProperty("$name.height")?.toIntOrNull()
        val baselineSize = props.getProperty("$name.size")?.toLongOrNull()
        val baselineHash = props.getProperty("$name.sha256")

        assertTrue("Missing width baseline for $name. Run tests with -Dui.smoke.updateBaseline=true", baselineWidth != null)
        assertTrue("Missing height baseline for $name. Run tests with -Dui.smoke.updateBaseline=true", baselineHeight != null)
        assertTrue("Missing size baseline for $name. Run tests with -Dui.smoke.updateBaseline=true", baselineSize != null)
        assertTrue("Missing hash baseline for $name. Run tests with -Dui.smoke.updateBaseline=true", !baselineHash.isNullOrBlank())

        assertTrue("Width mismatch for $name: expected=$baselineWidth actual=${image.width}", image.width == baselineWidth)
        assertTrue("Height mismatch for $name: expected=$baselineHeight actual=${image.height}", image.height == baselineHeight)

        val minSize = (baselineSize!! * (1.0 - sizeToleranceRatio)).toLong()
        val maxSize = (baselineSize * (1.0 + sizeToleranceRatio)).toLong()
        assertTrue(
            "Size drift for $name: baseline=$baselineSize actual=$size tolerance=${(sizeToleranceRatio * 100).toInt()}%",
            size in minSize..maxSize
        )
        assertTrue(
            "Hash mismatch for $name. Run with -Dui.smoke.updateBaseline=true only if UI change is intentional.",
            hash == baselineHash
        )
    }

    private fun loadBaselineProperties(): Properties {
        val props = Properties()
        if (baselineFile.exists()) {
            FileInputStream(baselineFile).use { props.load(it) }
        }
        return props
    }

    private fun saveBaselineProperties(props: Properties) {
        baselineFile.parentFile?.mkdirs()
        FileOutputStream(baselineFile).use { output ->
            props.store(output, "UI smoke screenshot baseline")
        }
    }

    private fun sha256(file: File): String {
        val digest = MessageDigest.getInstance("SHA-256")
        file.inputStream().use { input ->
            val buffer = ByteArray(DEFAULT_BUFFER_SIZE)
            while (true) {
                val read = input.read(buffer)
                if (read <= 0) break
                digest.update(buffer, 0, read)
            }
        }
        return digest.digest().joinToString("") { "%02x".format(it) }
    }
}
