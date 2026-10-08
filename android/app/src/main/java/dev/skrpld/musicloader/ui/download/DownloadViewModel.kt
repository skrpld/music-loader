package dev.skrpld.musicloader.ui.download

import androidx.compose.runtime.derivedStateOf
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import dev.skrpld.musicloader.data.ApiException
import dev.skrpld.musicloader.data.JobOptions
import dev.skrpld.musicloader.data.LinkScan
import dev.skrpld.musicloader.data.Links
import dev.skrpld.musicloader.data.LyricsMode
import dev.skrpld.musicloader.data.NotConfiguredException
import dev.skrpld.musicloader.data.ServerRepository
import dev.skrpld.musicloader.data.SettingsStore
import kotlin.coroutines.cancellation.CancellationException
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.receiveAsFlow
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch

sealed interface DownloadEvent {
    data class Submitted(val jobId: String, val rejected: Int) : DownloadEvent

    data class Failed(val message: String?, val unauthorized: Boolean, val notConfigured: Boolean) : DownloadEvent
}

class DownloadViewModel(
    private val repository: ServerRepository,
    private val settingsStore: SettingsStore,
) : ViewModel() {
    var linksText by mutableStateOf("")
        private set

    val scan: LinkScan by derivedStateOf { Links.scan(linksText) }

    /** Remembered options; "recheck" is per job and lives in [recheck]. */
    val options: StateFlow<JobOptions> = settingsStore.settings
        .map { it.jobOptions }
        .stateIn(viewModelScope, SharingStarted.Eagerly, JobOptions())

    var recheck by mutableStateOf(false)

    var submitting by mutableStateOf(false)
        private set

    private val _events = Channel<DownloadEvent>(Channel.BUFFERED)
    val events: Flow<DownloadEvent> = _events.receiveAsFlow()

    private val _shareReceived = MutableSharedFlow<Unit>(extraBufferCapacity = 1)

    /** Fires when links arrive from another app, so the UI can switch to this screen. */
    val shareReceived: SharedFlow<Unit> = _shareReceived

    fun updateLinks(text: String) {
        linksText = text
    }

    fun clearLinks() {
        linksText = ""
    }

    fun appendText(text: String) {
        val addition = text.trim()
        if (addition.isEmpty()) return
        linksText = if (linksText.isBlank()) addition else linksText.trimEnd() + "\n" + addition
    }

    fun receiveShared(text: String) {
        val links = Links.extract(text)
        if (links.isEmpty()) return
        val existing = linksText.split(Regex("\\s+")).filter { it.isNotBlank() }
        linksText = (existing + links).distinct().joinToString("\n")
        _shareReceived.tryEmit(Unit)
    }

    fun setLyricsMode(mode: LyricsMode) = updateOptions { it.copy(lyrics = mode.wire) }

    fun setSoundcloudReposts(enabled: Boolean) = updateOptions { it.copy(soundcloudReposts = enabled) }

    fun setSoundcloudLikes(enabled: Boolean) = updateOptions { it.copy(soundcloudLikes = enabled) }

    fun setSoundcloudFallback(enabled: Boolean) = updateOptions { it.copy(soundcloudFallback = enabled) }

    private fun updateOptions(change: (JobOptions) -> JobOptions) {
        viewModelScope.launch { settingsStore.setJobOptions(change(options.value)) }
    }

    fun submit() {
        val links = scan.links
        if (links.isEmpty() || submitting) return
        submitting = true
        viewModelScope.launch {
            try {
                val response = repository.submit(links, options.value.copy(recheck = recheck))
                linksText = ""
                recheck = false
                _events.send(DownloadEvent.Submitted(response.job.id, response.rejected.size))
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                _events.send(
                    DownloadEvent.Failed(
                        message = e.message,
                        unauthorized = e is ApiException && e.isUnauthorized,
                        notConfigured = e is NotConfiguredException,
                    ),
                )
            } finally {
                submitting = false
            }
        }
    }
}
