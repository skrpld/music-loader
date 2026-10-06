package dev.skrpld.musicloader.data

import kotlinx.coroutines.flow.Flow

enum class ThemeMode { System, Light, Dark }

/** Where the music is downloaded: by the app itself, or by a `music-loader serve` computer. */
enum class DownloadMode { Phone, Server }

data class AppSettings(
    val mode: DownloadMode = DownloadMode.Phone,
    /** Library folder for [DownloadMode.Phone]; empty means the shared Music folder. */
    val musicDir: String = "",
    val serverUrl: String = "",
    val token: String = "",
    val themeMode: ThemeMode = ThemeMode.System,
    val dynamicColor: Boolean = true,
    /** Options preselected for the next job; "recheck" is never remembered. */
    val jobOptions: JobOptions = JobOptions(),
) {
    val server: ServerConfig?
        get() = if (serverUrl.isNotBlank() && token.isNotBlank()) ServerConfig(serverUrl, token) else null
}

interface SettingsStore {
    val settings: Flow<AppSettings>

    suspend fun setMode(mode: DownloadMode)

    suspend fun setMusicDir(path: String)

    suspend fun setServer(url: String, token: String)

    suspend fun setThemeMode(mode: ThemeMode)

    suspend fun setDynamicColor(enabled: Boolean)

    suspend fun setJobOptions(options: JobOptions)
}
