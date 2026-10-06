package dev.skrpld.musicloader.ui.settings

import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import dev.skrpld.musicloader.data.ApiException
import dev.skrpld.musicloader.data.AppSettings
import dev.skrpld.musicloader.data.DownloadMode
import dev.skrpld.musicloader.data.LocalBackend
import dev.skrpld.musicloader.data.ServerConfig
import dev.skrpld.musicloader.data.ServerInfo
import dev.skrpld.musicloader.data.ServerRepository
import dev.skrpld.musicloader.data.ServerUrls
import dev.skrpld.musicloader.data.SettingsStore
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

    private var testJob: Job? = null

    init {
        viewModelScope.launch {
            val stored = settingsStore.settings.first()
            // Keep anything typed while the settings were loading.
            if (urlInput.isEmpty()) urlInput = stored.serverUrl
            if (tokenInput.isEmpty()) tokenInput = stored.token
            if (musicDirInput.isEmpty()) musicDirInput = stored.musicDir
        }
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

    fun setThemeMode(mode: ThemeMode) {
        viewModelScope.launch { settingsStore.setThemeMode(mode) }
    }

    fun setDynamicColor(enabled: Boolean) {
        viewModelScope.launch { settingsStore.setDynamicColor(enabled) }
    }
}
