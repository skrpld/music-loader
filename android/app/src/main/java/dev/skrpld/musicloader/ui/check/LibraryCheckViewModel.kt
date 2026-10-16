package dev.skrpld.musicloader.ui.check

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import dev.skrpld.musicloader.data.CheckRule
import dev.skrpld.musicloader.data.Job
import dev.skrpld.musicloader.data.ServerRepository
import dev.skrpld.musicloader.data.ServerState
import dev.skrpld.musicloader.data.SettingsStore
import dev.skrpld.musicloader.data.serverState
import kotlin.coroutines.cancellation.CancellationException
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.mapLatest
import kotlinx.coroutines.flow.receiveAsFlow
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

data class LibraryCheckState(
    /** Rules for the next check. */
    val rules: Set<CheckRule> = CheckRule.entries.toSet(),
    val submitting: Boolean = false,
)

/**
 * Settings → Check the library: starts check jobs (and their fix) on the server or the
 * phone and shows the newest one, with its full report once it has finished.
 */
class LibraryCheckViewModel(
    private val repository: ServerRepository,
    private val settingsStore: SettingsStore,
) : ViewModel() {
    private val _state = MutableStateFlow(LibraryCheckState())
    val state: StateFlow<LibraryCheckState> = _state.asStateFlow()

    private val _failures = Channel<String?>(Channel.BUFFERED)

    /** Messages of requests that failed, for a snackbar. */
    val failures: Flow<String?> = _failures.receiveAsFlow()

    /** The newest check job as the event stream reports it (the running job with its progress). */
    private val latest: StateFlow<Job?> = repository.connection
        .map { latestCheck(it.serverState) }
        .distinctUntilChanged()
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), null)

    /** The finished job's detail: the summary in the stream carries only the report's numbers. */
    @OptIn(ExperimentalCoroutinesApi::class)
    private val detail: StateFlow<Job?> = latest
        .map { job -> job?.takeIf { it.status.isFinished }?.let { it.id to it.finishedAt } }
        .distinctUntilChanged()
        .mapLatest { key ->
            if (key == null) return@mapLatest null
            try {
                repository.job(key.first)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                null
            }
        }
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), null)

    /** The check to show: its detail once loaded, otherwise what the stream has. */
    val job: StateFlow<Job?> = combine(latest, detail) { summary, full ->
        if (full != null && full.id == summary?.id) full else summary
    }.stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), null)

    fun toggleRule(rule: CheckRule) = _state.update {
        it.copy(rules = if (rule in it.rules) it.rules - rule else it.rules + rule)
    }

    /** Checks the library with the chosen rules. */
    fun check() = submit(fix = false, rules = _state.value.rules)

    /** Fixes what the shown report found, with the rules that report used. */
    fun fix() {
        val reported = CheckRule.fromWire(job.value?.check?.rules)
        submit(fix = true, rules = reported.ifEmpty { _state.value.rules })
    }

    fun cancel() {
        val id = job.value?.id ?: return
        viewModelScope.launch {
            try {
                repository.cancel(id)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                _failures.trySend(e.message)
            }
        }
    }

    private fun submit(fix: Boolean, rules: Set<CheckRule>) {
        if (rules.isEmpty() || _state.value.submitting) return
        _state.update { it.copy(submitting = true) }
        viewModelScope.launch {
            try {
                // Lyrics and formatting settings decide how the fix rewrites tracks.
                val options = settingsStore.settings.first().jobOptions.copy(
                    fix = fix,
                    rules = CheckRule.entries.filter { it in rules }.map { it.wire },
                )
                repository.submitCheck(options)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                _failures.trySend(e.message)
            } finally {
                _state.update { it.copy(submitting = false) }
            }
        }
    }

    private companion object {
        fun latestCheck(state: ServerState?): Job? {
            state ?: return null
            val newest = state.jobs.filter { it.isCheck }.maxByOrNull { it.createdAt } ?: return null
            // The running job comes with its progress (files) only as `active`.
            return state.active?.takeIf { it.id == newest.id } ?: newest
        }
    }
}
