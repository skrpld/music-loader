package dev.skrpld.musicloader.ui.jobs

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import dev.skrpld.musicloader.data.ApiException
import dev.skrpld.musicloader.data.Connection
import dev.skrpld.musicloader.data.Job
import dev.skrpld.musicloader.data.ServerRepository
import dev.skrpld.musicloader.data.serverState
import kotlin.coroutines.cancellation.CancellationException
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.receiveAsFlow
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch

/** The job shown in the detail pane. */
data class SelectedJob(
    val id: String,
    val job: Job?,
    /** True while the running job is followed live through the event stream. */
    val live: Boolean,
    val loading: Boolean,
    /** The server no longer knows the job (deleted elsewhere). */
    val gone: Boolean,
)

sealed interface JobsEvent {
    data class ActionFailed(val message: String?) : JobsEvent
}

class JobsViewModel(private val repository: ServerRepository) : ViewModel() {
    val connection: StateFlow<Connection> = repository.connection

    private val selectedId = MutableStateFlow<String?>(null)
    val selectedJobId: StateFlow<String?> = selectedId.asStateFlow()

    private val fetched = MutableStateFlow<Job?>(null)
    private val loading = MutableStateFlow(false)

    private val _events = Channel<JobsEvent>(Channel.BUFFERED)
    val events: Flow<JobsEvent> = _events.receiveAsFlow()

    val selectedJob: StateFlow<SelectedJob?> =
        combine(selectedId, connection, fetched, loading) { id, connection, detail, isLoading ->
            if (id == null) return@combine null
            val state = connection.serverState
            val active = state?.active?.takeIf { it.id == id }
            val summary = state?.jobs?.firstOrNull { it.id == id }
            val full = detail?.takeIf { it.id == id }
            SelectedJob(
                id = id,
                job = active ?: full ?: summary,
                live = active != null,
                loading = isLoading && active == null && full == null,
                gone = state != null && summary == null && active == null && full == null && !isLoading,
            )
        }.stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), null)

    init {
        // The full log and error list of a job that is not running come from the
        // detail endpoint; fetch it again whenever the job changes state.
        viewModelScope.launch {
            combine(selectedId, connection) { id, connection ->
                val summary = connection.serverState?.jobs?.firstOrNull { it.id == id }
                DetailKey(id, summary?.statusWire, summary?.finishedAt)
            }.distinctUntilChanged().collectLatest { key ->
                val id = key.id
                if (id == null) {
                    fetched.value = null
                    return@collectLatest
                }
                if (fetched.value?.id != id) fetched.value = null
                loading.value = true
                try {
                    fetched.value = repository.job(id)
                } catch (e: CancellationException) {
                    throw e
                } catch (e: ApiException) {
                    if (e.isNotFound) fetched.value = null
                } catch (e: Exception) {
                    // Offline: keep what is shown; the next state change fetches again.
                } finally {
                    loading.value = false
                }
            }
        }
    }

    fun select(id: String?) {
        // A job that was just submitted may not be in the event stream yet: count it as
        // loading until the detail request answers instead of treating it as deleted.
        if (id != null && id != selectedId.value) loading.value = true
        selectedId.value = id
    }

    fun retry() = repository.retryNow()

    fun cancel(id: String) = action { repository.cancel(id) }

    fun delete(id: String) = action {
        repository.delete(id)
        if (selectedId.value == id) selectedId.value = null
    }

    private fun action(block: suspend () -> Unit) {
        viewModelScope.launch {
            try {
                block()
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                _events.send(JobsEvent.ActionFailed(e.message))
            }
        }
    }

    private data class DetailKey(val id: String?, val status: String?, val finishedAt: Long?)
}
