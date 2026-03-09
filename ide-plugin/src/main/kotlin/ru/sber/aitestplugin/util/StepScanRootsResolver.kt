package ru.sber.aitestplugin.util

import com.intellij.openapi.diagnostic.Logger
import com.intellij.openapi.project.Project
import com.intellij.openapi.roots.OrderEnumerator
import com.intellij.openapi.vfs.VfsUtilCore
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.Paths

object StepScanRootsResolver {
    private val logger = Logger.getInstance(StepScanRootsResolver::class.java)

    fun resolveAdditionalRoots(project: Project, projectRoot: String): List<String> {
        val rawRoots = buildList {
            OrderEnumerator.orderEntries(project)
                .librariesOnly()
                .withoutSdk()
                .sources()
                .roots
                .forEach { root ->
                    add(VfsUtilCore.urlToPath(root.url))
                }
        }
        return normalizeAdditionalRoots(projectRoot, rawRoots)
    }

    internal fun normalizeAdditionalRoots(projectRoot: String, rawRoots: Iterable<String>): List<String> {
        val primary = normalizePath(projectRoot)
        val roots = linkedSetOf<String>()

        rawRoots.forEach { rawRoot ->
            val candidate = normalizePath(rawRoot) ?: return@forEach
            if (primary != null && candidate == primary) return@forEach
            roots.add(candidate)
        }

        logResolvedRoots(primary, roots)
        return roots.toList()
    }

    private fun normalizePath(raw: String): String? {
        val trimmed = raw.trim().removeSuffix("!/")
        if (trimmed.isBlank()) return null
        return try {
            val path = Paths.get(trimmed).normalize()
            if (!Files.exists(path)) return null
            if (!isSupported(path)) return null
            path.toString()
        } catch (_: Exception) {
            null
        }
    }

    private fun isSupported(path: Path): Boolean {
        if (Files.isDirectory(path)) return true
        if (!Files.isRegularFile(path)) return false
        return path.fileName.toString().endsWith(".jar", ignoreCase = true)
    }

    private fun logResolvedRoots(primary: String?, roots: Set<String>) {
        val directoryCount = roots.count { value ->
            runCatching { Files.isDirectory(Paths.get(value)) }.getOrDefault(false)
        }
        val jarCount = roots.size - directoryCount

        logger.info(
            "Resolved ${roots.size} additional scan roots for primary=${primary ?: "<empty>"} " +
                "(directories=$directoryCount, jars=$jarCount)"
        )

        if (logger.isDebugEnabled) {
            roots.forEach { root ->
                logger.debug("Additional scan root: $root")
            }
        }
    }
}
