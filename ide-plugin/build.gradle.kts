import org.gradle.api.GradleException
import org.gradle.api.tasks.compile.JavaCompile
import org.gradle.language.jvm.tasks.ProcessResources

plugins {
    id("org.jetbrains.intellij.platform") version "2.11.0"
    kotlin("jvm") version "2.1.0"
}

kotlin {
    jvmToolchain(17)
}

group = "ru.sber"
version = "0.2.0-SNAPSHOT"

val sberIdePath = providers.gradleProperty("sberIdePath").orNull?.trim()?.takeIf { it.isNotEmpty() }
val sberIdeHomePath = sberIdePath?.let { rawPath ->
    val supplied = file(rawPath)
    val ideHome = if (supplied.isFile && supplied.extension.equals("exe", ignoreCase = true)) {
        // Accept both IDE home and direct .../bin/idea64.exe path.
        supplied.parentFile?.parentFile ?: supplied.parentFile
    } else {
        supplied
    }
    ideHome?.absolutePath
}

repositories {
    mavenCentral()
    intellijPlatform {
        defaultRepositories()
    }
}

dependencies {
    implementation("com.fasterxml.jackson.module:jackson-module-kotlin:2.17.1")
    implementation("com.fasterxml.jackson.datatype:jackson-datatype-jsr310:2.17.1")
    implementation("com.squareup.okhttp3:okhttp:4.12.0")

    testImplementation(kotlin("test"))
    testImplementation("junit:junit:4.13.2")
    testRuntimeOnly("org.junit.vintage:junit-vintage-engine:5.11.4")

    intellijPlatform {
        if (sberIdeHomePath != null) {
            local(sberIdeHomePath)
        } else {
            intellijIdea("2025.1") {
                useInstaller.set(false)
            }
        }
        bundledPlugin("com.intellij.java")
        jetbrainsRuntime()
    }
}

intellijPlatform {
    pluginConfiguration {
        changeNotes = "Update plugin metadata for 2025.1 IDE builds."
        ideaVersion {
            sinceBuild = "251"
        }
    }
}

val mojibakeMarkers = listOf(
    "РџР",
    "РЎР",
    "РќР",
    "РђР",
    "Р”Р",
    "РС",
    "РћС",
    "РљР",
    "СЃС",
    "С‚С",
    "Ð",
    "Ñ"
)

val checkEncodingHealth by tasks.registering {
    group = "verification"
    description = "Fails if source/resource files contain mojibake markers."
    doLast {
        val hits = mutableListOf<String>()
        fileTree("src/main") {
            include("**/*.kt", "**/*.xml", "**/*.properties")
        }
            .files
            .sortedBy { it.path }
            .forEach { file ->
                file.readLines(Charsets.UTF_8).forEachIndexed { index, line ->
                    if (mojibakeMarkers.any(line::contains)) {
                        val relative = file.relativeTo(projectDir).invariantSeparatorsPath
                        hits += "$relative:${index + 1}: ${line.trim()}"
                    }
                }
            }

        if (hits.isNotEmpty()) {
            val preview = hits.take(30).joinToString("\n")
            val suffix = if (hits.size > 30) "\n... and ${hits.size - 30} more" else ""
            throw GradleException(
                "Detected possible mojibake artifacts. Save files in UTF-8.\n$preview$suffix"
            )
        }
    }
}

tasks {
    withType<JavaCompile>().configureEach {
        options.encoding = "UTF-8"
    }
    withType<ProcessResources>().configureEach {
        filteringCharset = "UTF-8"
    }
    test {
        useJUnitPlatform()
        val sandboxRoot = layout.buildDirectory.dir("test-sandbox").get().asFile
        val configRoot = sandboxRoot.resolve("config")
        val systemRoot = sandboxRoot.resolve("system")
        val pluginsRoot = sandboxRoot.resolve("plugins")
        val logRoot = sandboxRoot.resolve("log")
        val tmpRoot = sandboxRoot.resolve("tmp")

        doFirst {
            listOf(
                configRoot,
                systemRoot,
                pluginsRoot,
                logRoot,
                tmpRoot,
            ).forEach { it.mkdirs() }
        }

        // Keep IntelliJ test runs inside a local sandbox rather than the user's
        // default IDEA config/system directories.
        systemProperty("idea.config.path", configRoot.absolutePath)
        systemProperty("idea.system.path", systemRoot.absolutePath)
        systemProperty("idea.plugins.path", pluginsRoot.absolutePath)
        systemProperty("idea.log.path", logRoot.absolutePath)
        systemProperty("java.io.tmpdir", tmpRoot.absolutePath)
    }
    named("check") {
        dependsOn(checkEncodingHealth)
    }
}
