package dev.skrpld.musicloader.ui.settings

import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import dev.skrpld.musicloader.data.ApiException
import dev.skrpld.musicloader.data.AppSettings
import dev.skrpld.musicloader.data.DownloadMode
import dev.skrpld.musicloader.data.FeatPlacement
import dev.skrpld.musicloader.data.FilenameTemplate
import dev.skrpld.musicloader.data.JobOptions
import dev.skrpld.musicloader.data.LocalBackend
import dev.skrpld.musicloader.data.LyricsProvider
import dev.skrpld.musicloader.data.LyricsRetry
import dev.skrpld.musicloader.data.LyricsTolerance
import dev.skrpld.musicloader.data.ServerConfig
import dev.skrpld.musicloader.data.ServerInfo
import dev.skrpld.musicloader.data.ServerRepository
import dev.skrpld.musicloader.data.ServerUrls
import dev.skrpld.musicloader.data.SettingsStore
import dev.skrpld.musicloader.data.SpotifyCredentials
import dev.skrpld.musicloader.data.ThemeMode
import kotlin.coroutines.cancellation.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch

sealed interface ConnectionTest {
    data object Idle : ConnectionTest

    data object Running : ConnectionTest

    data class Success(val info: ServerInfo) : ConnectionTest

    data class Failure(val message: String?, val unauthorized: Boolean) : ConnectionTest
}

class SettingsViewModel(
    private val repository: ServerRepository,
    private val settingsStore: SettingsStore,
    private val local: LocalBackend,
) : ViewModel() {
    val settings: StateFlow<AppSettings?> =
        settingsStore.settings.stateIn(viewModelScope, SharingStarted.Eagerly, null)

    var urlInput by mutableStateOf("")
        private set
    var tokenInput by mutableStateOf("")
        private set
    var test by mutableStateOf<ConnectionTest>(ConnectionTest.Idle)
        private set

    /** Phone mode: the folder being edited; empty means [defaultMusicDir]. */
    var musicDirInput by mutableStateOf("")
        private set
    var storageGranted by mutableStateOf(local.hasStorageAccess())
        private set

    val defaultMusicDir: String get() = local.defaultMusicDir

    /** Phone mode: the Spotify application credentials being edited (stored encrypted). */
    var spotifyIdInput by mutableStateOf("")
        private set
    var spotifySecretInput by mutableStateOf("")
        private set
    var spotifySaveFailed by mutableStateOf(false)
        private set

    val spotifyCredentials: StateFlow<SpotifyCredentials?> =
        settingsStore.spotifyCredentials.stateIn(viewModelScope, SharingStarted.Eagerly, null)

    /** SoundCloud file name template being edited. */
    var filenameInput by mutableStateOf("")
        private set

    private var testJob: Job? = null

    init {
        viewModelScope.launch {
            val stored = settingsStore.settings.first()
            // Keep anything typed while the settings were loading.
            if (urlInput.isEmpty()) urlInput = stored.serverUrl
            if (tokenInput.isEmpty()) tokenInput = stored.token
            if (musicDirInput.isEmpty()) musicDirInput = stored.musicDir
            if (filenameInput.isEmpty()) filenameInput = stored.jobOptions.soundcloudFilename
            val credentials = settingsStore.spotifyCredentials.first()
            if (spotifyIdInput.isEmpty()) spotifyIdInput = credentials.clientId
            if (spotifySecretInput.isEmpty()) spotifySecretInput = credentials.clientSecret
        }
    }

    fun updateSpotifyId(value: String) {
        spotifyIdInput = value
        spotifySaveFailed = false
    }

    fun updateSpotifySecret(value: String) {
        spotifySecretInput = value
        spotifySaveFailed = false
    }

    /** Both fields filled (use the official API) or both empty (the built-in client). */
    val spotifyValid: Boolean
        get() = spotifyIdInput.isBlank() == spotifySecretInput.isBlank()

    fun isSpotifySaved(saved: SpotifyCredentials?): Boolean =
        saved != null && saved.clientId == spotifyIdInput.trim() && saved.clientSecret == spotifySecretInput.trim()

    fun saveSpotify() {
        if (!spotifyValid) return
        val credentials = SpotifyCredentials(spotifyIdInput.trim(), spotifySecretInput.trim())
        spotifyIdInput = credentials.clientId
        spotifySecretInput = credentials.clientSecret
        viewModelScope.launch {
            try {
                settingsStore.setSpotifyCredentials(credentials)
                spotifySaveFailed = false
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                // The Keystore refused (no secure lock screen policy, a reset keystore): nothing was stored.
                spotifySaveFailed = true
            }
        }
    }

    fun clearSpotify() {
        spotifyIdInput = ""
        spotifySecretInput = ""
        saveSpotify()
    }

    fun setMode(mode: DownloadMode) {
        viewModelScope.launch { settingsStore.setMode(mode) }
    }

    /** Called when the app returns from the permission screens. */
    fun refreshStorageAccess() {
        val granted = local.hasStorageAccess()
        if (granted != storageGranted) {
            storageGranted = granted
            repository.retryNow()
        }
    }

    fun updateMusicDir(value: String) {
        musicDirInput = value
    }

    /** An absolute folder path, or empty for the default. */
    val musicDirValid: Boolean
        get() = musicDirInput.isBlank() || musicDirInput.trim().startsWith("/")

    fun isMusicDirSaved(current: AppSettings?): Boolean =
        current != null && current.musicDir == musicDirInput.trim().trimEnd('/')

    fun saveMusicDir() {
        if (!musicDirValid) return
        val path = musicDirInput.trim().trimEnd('/')
        musicDirInput = path
        viewModelScope.launch { settingsStore.setMusicDir(path) }
    }

    fun pickMusicDir(path: String) {
        musicDirInput = path
        saveMusicDir()
    }

    val normalizedUrl: String? get() = ServerUrls.normalize(urlInput)
    val tokenValid: Boolean get() = ServerUrls.isValidToken(tokenInput)
    val canSubmit: Boolean get() = normalizedUrl != null && tokenValid

    fun isSaved(current: AppSettings?): Boolean =
        current != null && current.serverUrl == normalizedUrl && current.token == tokenInput.trim()

    fun updateUrl(value: String) {
        urlInput = value
        resetTest()
    }

    fun updateToken(value: String) {
        tokenInput = value
        resetTest()
    }

    private fun resetTest() {
        testJob?.cancel()
        test = ConnectionTest.Idle
    }

    fun testConnection() {
        val url = normalizedUrl ?: return
        if (!tokenValid) return
        testJob?.cancel()
        test = ConnectionTest.Running
        testJob = viewModelScope.launch {
            test = try {
                ConnectionTest.Success(repository.test(ServerConfig(url, tokenInput.trim())))
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                ConnectionTest.Failure(e.message, e is ApiException && e.isUnauthorized)
            }
        }
    }

    fun save() {
        val url = normalizedUrl ?: return
        if (!tokenValid) return
        val token = tokenInput.trim()
        urlInput = url
        tokenInput = token
        viewModelScope.launch {
            settingsStore.setServer(url, token)
            repository.retryNow()
        }
    }

    /** The formatting settings live with the job options and go out with every job. */
    private fun updateJobOptions(change: (JobOptions) -> JobOptions) {
        viewModelScope.launch {
            settingsStore.setJobOptions(change(settingsStore.settings.first().jobOptions))
        }
    }

    fun setFeatPlacement(placement: FeatPlacement) = updateJobOptions { it.copy(soundcloudFeat = placement.wire) }

    fun setKeepProducers(keep: Boolean) = updateJobOptions { it.copy(soundcloudKeepProducers = keep) }

    fun setKeepVersions(keep: Boolean) = updateJobOptions { it.copy(soundcloudKeepVersions = keep) }

    fun setStripPromo(strip: Boolean) = updateJobOptions { it.copy(soundcloudStripPromo = strip) }

    /** Turns a lyrics provider on (asked last) or off; the last one stays on. */
    fun setLyricsProvider(provider: LyricsProvider, enabled: Boolean) = updateJobOptions { options ->
        val current = options.providers
        val next = if (enabled) (current - provider) + provider else current - provider
        if (next.isEmpty()) options else options.copy(lyricsProviders = next.map { it.wire })
    }

    /** Asks an enabled lyrics provider one place earlier. */
    fun moveLyricsProviderUp(provider: LyricsProvider) = updateJobOptions { options ->
        val current = options.providers.toMutableList()
        val index = current.indexOf(provider)
        if (index <= 0) return@updateJobOptions options
        current[index] = current[index - 1]
        current[index - 1] = provider
        options.copy(lyricsProviders = current.map { it.wire })
    }

    fun setLyricsPlain(plain: Boolean) = updateJobOptions { it.copy(lyricsPlain = plain) }

    fun setLyricsOriginalForVersions(allow: Boolean) = updateJobOptions { it.copy(lyricsOriginalForVersions = allow) }

    fun setLyricsLengthTolerance(seconds: Double) = updateJobOptions {
        it.copy(lyricsLengthTolerance = LyricsTolerance.sanitize(seconds))
    }

    fun setLyricsRetryDays(days: Int) = updateJobOptions { it.copy(lyricsRetryDays = LyricsRetry.sanitize(days)) }

    fun updateFilename(value: String) {
        filenameInput = value
    }

    val filenameValid: Boolean get() = FilenameTemplate.isValid(filenameInput.trim())

    fun isFilenameSaved(current: AppSettings?): Boolean =
        current != null && current.jobOptions.soundcloudFilename == filenameInput.trim()

    fun saveFilename() {
        if (!filenameValid) return
        val template = filenameInput.trim()
        filenameInput = template
        updateJobOptions { it.copy(soundcloudFilename = template) }
    }

    fun resetFilename() {
        filenameInput = FilenameTemplate.DEFAULT
        saveFilename()
    }

    fun setThemeMode(mode: ThemeMode) {
        viewModelScope.launch { settingsStore.setThemeMode(mode) }
    }

    fun setDynamicColor(enabled: Boolean) {
        viewModelScope.launch { settingsStore.setDynamicColor(enabled) }
    }
}
