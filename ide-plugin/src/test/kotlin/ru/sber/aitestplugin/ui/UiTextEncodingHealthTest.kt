package ru.sber.aitestplugin.ui

import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

class UiTextEncodingHealthTest {

    @Test
    fun `ui sources do not contain suspicious cyrillic characters`() {
        val allowedRussianChars = (('А'..'я').toSet() + setOf('Ё', 'ё'))
        val roots = listOf(
            File("src/main/kotlin/ru/sber/aitestplugin/ui"),
            File("src/main/resources/META-INF/plugin.xml")
        )
        val targets = roots.flatMap { root ->
            if (!root.exists()) {
                emptyList()
            } else if (root.isFile) {
                listOf(root)
            } else {
                root.walkTopDown().filter { it.isFile && (it.extension == "kt" || it.extension == "xml" || it.extension == "properties") }.toList()
            }
        }

        val hits = mutableListOf<String>()
        targets.forEach { file ->
            file.readLines(Charsets.UTF_8).forEachIndexed { index, line ->
                val hasSuspiciousCyrillic = line.any { ch -> ch in '\u0400'..'\u04FF' && ch !in allowedRussianChars }
                if (hasSuspiciousCyrillic) {
                    hits += "${file.invariantSeparatorsPath}:${index + 1}: ${line.trim()}"
                }
            }
        }

        assertTrue(
            "Suspicious cyrillic characters found in UI texts:\n${hits.joinToString("\n")}",
            hits.isEmpty()
        )
    }
}
