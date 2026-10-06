import java.io.InputStream
import java.util.zip.ZipEntry
import java.util.zip.ZipFile
import java.util.zip.ZipInputStream
import java.util.zip.ZipOutputStream
import org.jetbrains.kotlin.gradle.dsl.JvmTarget

plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.compose)
    alias(libs.plugins.kotlin.serialization)
    alias(libs.plugins.chaquopy)
}

// The phone mode runs ffmpeg and QuickJS, which exist for this ABI; so does the app.
val nativeAbi = "arm64-v8a"

// Release signing comes from the environment (see .github/workflows/android.yml);
// without it the release build stays unsigned and CI ships the debug build.
val releaseKeystore = providers.environmentVariable("ANDROID_KEYSTORE_PATH").orNull

android {
    namespace = "dev.skrpld.musicloader"
    compileSdk = 37

    defaultConfig {
        applicationId = "dev.skrpld.musicloader"
        minSdk = 26
        // 37 adds local network protection; the app talks to a server in the local network.
        targetSdk = 36
        versionCode = providers.environmentVariable("GITHUB_RUN_NUMBER").orNull?.toIntOrNull() ?: 1
        versionName = providers.environmentVariable("VERSION_NAME")
            .orElse(providers.gradleProperty("appVersionName"))
            .get()

        ndk {
            abiFilters += nativeAbi
        }
    }

    signingConfigs {
        if (releaseKeystore != null) {
            create("release") {
                storeFile = file(releaseKeystore)
                storePassword = providers.environmentVariable("ANDROID_KEYSTORE_PASSWORD").get()
                keyAlias = providers.environmentVariable("ANDROID_KEY_ALIAS").get()
                keyPassword = providers.environmentVariable("ANDROID_KEY_PASSWORD").get()
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
            signingConfig = signingConfigs.findByName("release")
        }
        debug {
            applicationIdSuffix = ".debug"
            versionNameSuffix = "-debug"
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlin {
        compilerOptions {
            jvmTarget = JvmTarget.fromTarget("17")
        }
    }

    buildFeatures {
        compose = true
        buildConfig = true
    }

    packaging {
        resources {
            excludes += "/META-INF/{AL2.0,LGPL2.1}"
        }
        jniLibs {
            // ffmpeg, ffprobe and QuickJS are executables: Android only runs them from the
            // app's native library folder, so the libraries are installed as files.
            useLegacyPackaging = true
            // Prebuilt and partly not ELF files (the zips): packaged as they are.
            keepDebugSymbols += NativeToolsTask.OUTPUTS.map { "**/$it" }
        }
    }
}

chaquopy {
    defaultConfig {
        version = "3.13"
        pip {
            options("--no-deps")
            options("--find-links", file("wheels").absolutePath)
            install("-r", "requirements-android.txt")
        }
    }
    sourceSets {
        // The music_loader package from cli/ - the same code as the desktop tool -
        // next to the app's own modules in src/main/python.
        getByName("main") {
            srcDir("../../cli")
            include("music_loader/**", "curl_cffi/**", "pymongo/**")
        }
    }
}

/**
 * Unpacks ffmpeg, ffprobe and QuickJS from the youtubedl-android packages (Termux builds
 * repackaged as native libraries), plus the few libraries their ffmpeg build takes from
 * the Python package there, into a jniLibs folder. See music_loader/android.py.
 */
abstract class NativeToolsTask : DefaultTask() {
    @get:InputFiles
    @get:PathSensitive(PathSensitivity.NAME_ONLY)
    abstract val archives: ConfigurableFileCollection

    @get:Input
    abstract val abi: Property<String>

    @get:OutputDirectory
    abstract val outputDir: DirectoryProperty

    @TaskAction
    fun unpack() {
        val abiDir = outputDir.get().asFile.resolve(abi.get())
        abiDir.deleteRecursively()
        abiDir.mkdirs()
        val prefix = "jni/${abi.get()}/"
        archives.forEach { aar ->
            ZipFile(aar).use { zip ->
                for (entry in zip.entries()) {
                    if (!entry.name.startsWith(prefix)) continue
                    when (val name = entry.name.removePrefix(prefix)) {
                        in TOOLS -> zip.getInputStream(entry).use { input ->
                            abiDir.resolve(name).outputStream().use { input.copyTo(it) }
                        }
                        PYTHON_ARCHIVE -> zip.getInputStream(entry).use { input ->
                            repackDependencies(input, abiDir.resolve(DEPENDENCIES_ARCHIVE))
                        }
                    }
                }
            }
        }
        val missing = OUTPUTS.filterNot { abiDir.resolve(it).isFile }
        if (missing.isNotEmpty()) throw GradleException("Native tools not found: ${missing.joinToString()}")
    }

    private fun repackDependencies(source: InputStream, target: java.io.File) {
        val found = mutableSetOf<String>()
        ZipInputStream(source).use { input ->
            ZipOutputStream(target.outputStream()).use { output ->
                while (true) {
                    val entry = input.nextEntry ?: break
                    val name = DEPENDENCIES[entry.name] ?: continue
                    output.putNextEntry(ZipEntry(name))
                    input.copyTo(output)
                    output.closeEntry()
                    found += name
                }
            }
        }
        val missing = DEPENDENCIES.values - found
        if (missing.isNotEmpty()) throw GradleException("ffmpeg libraries not found: ${missing.joinToString()}")
    }

    companion object {
        private val TOOLS = setOf("libffmpeg.so", "libffprobe.so", "libffmpeg.zip.so", "libqjs.so")
        private const val PYTHON_ARCHIVE = "libpython.zip.so"
        private const val DEPENDENCIES_ARCHIVE = "libffmpegdeps.zip.so"

        // Entry in the Python package (the file, not its version links) -> library name.
        private val DEPENDENCIES = mapOf(
            "usr/lib/libandroid-posix-semaphore.so" to "libandroid-posix-semaphore.so",
            "usr/lib/libandroid-support.so" to "libandroid-support.so",
            "usr/lib/libc++_shared.so" to "libc++_shared.so",
            "usr/lib/libcrypto.so.3" to "libcrypto.so.3",
            "usr/lib/libexpat.so.1.11.1" to "libexpat.so.1",
        )

        val OUTPUTS = TOOLS + DEPENDENCIES_ARCHIVE
    }
}

val nativeToolArchives: Configuration by configurations.creating {
    isCanBeConsumed = false
    isTransitive = false
}

val nativeTools = tasks.register<NativeToolsTask>("nativeTools") {
    archives.from(nativeToolArchives)
    abi = nativeAbi
    outputDir = layout.buildDirectory.dir("generated/nativeTools")
}

androidComponents {
    onVariants { variant ->
        variant.sources.jniLibs?.addGeneratedSourceDirectory(nativeTools, NativeToolsTask::outputDir)
    }
}

dependencies {
    implementation(platform(libs.androidx.compose.bom))
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.activity.compose)
    implementation(libs.androidx.lifecycle.runtime.compose)
    implementation(libs.androidx.lifecycle.viewmodel.compose)
    implementation(libs.androidx.datastore.preferences)
    implementation(libs.androidx.compose.ui)
    implementation(libs.androidx.compose.material3)
    implementation(libs.androidx.compose.material3.navigation.suite)
    implementation(libs.kotlinx.coroutines.android)
    implementation(libs.kotlinx.serialization.json)
    implementation(libs.okhttp)
    implementation(libs.okhttp.sse)

    val youtubedlAndroid = libs.versions.youtubedlAndroid.get()
    nativeToolArchives("io.github.junkfood02.youtubedl-android:ffmpeg:$youtubedlAndroid@aar")
    nativeToolArchives("io.github.junkfood02.youtubedl-android:library:$youtubedlAndroid@aar")
}
