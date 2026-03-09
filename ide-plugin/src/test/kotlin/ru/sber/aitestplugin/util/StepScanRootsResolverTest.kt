package ru.sber.aitestplugin.util

import org.junit.Assert.assertEquals
import org.junit.Test
import java.nio.file.Files

class StepScanRootsResolverTest {

    @Test
    fun `normalizes supported roots and skips duplicates missing roots and project root`() {
        val projectRoot = Files.createTempDirectory("scan-project-root")
        val dependencyRoot = Files.createTempDirectory("scan-dependency-root")
        val jarPath = Files.createTempFile("scan-dependency-sources", ".jar")
        val missingPath = dependencyRoot.resolve("missing-sources")

        val result = StepScanRootsResolver.normalizeAdditionalRoots(
            projectRoot = projectRoot.toString(),
            rawRoots = listOf(
                projectRoot.toString(),
                "${dependencyRoot}!/",
                dependencyRoot.toString(),
                jarPath.toString(),
                missingPath.toString(),
                "   "
            )
        )

        assertEquals(
            listOf(
                dependencyRoot.toString(),
                jarPath.toString()
            ),
            result
        )
    }
}
