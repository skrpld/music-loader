package dev.skrpld.musicloader.data

import kotlin.coroutines.cancellation.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.flatMapLatest
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.withTimeoutOrNull

sealed interface Connection {
    data object NotConfigured : Connection

    data object Connecting : Connection

    data class Online(val state: ServerState) : Connection

    /** The stream is down; [lastState] is what the server reported before, if anything. */
    data class Offline(
        val unauthorized: Boolean,
        val message: String?,
        val lastState: ServerState?,
    ) : Connection
}

val Connection.serverState: ServerState?
    get() = when (this) {
        is Connection.Online -> state
        is Connection.Offline -> lastState
        else -> null
    }

class NotConfiguredException : IllegalStateException("The server is not configured")

/** Live server state over the event stream, reconnecting with backoff, plus the job actions. */
class ServerRepository(
    private val settingsStore: SettingsStore,
    private val api: MusicLoaderApi,
    scope: CoroutineScope,
) {
    private val retryRequests = MutableSharedFlow<Unit>(extraBufferCapacity = 1)

    @OptIn(ExperimentalCoroutinesApi::class)
    val connection: StateFlow<Connection> = settingsStore.settings
        .map { it.server }
        .distinctUntilChanged()
        .flatMapLatest { server -> if (server == null) flowOf(Connection.NotConfigured) else stream(server) }
        // The stream only runs while the UI is visible.
        .stateIn(scope, SharingStarted.WhileSubscribed(5_000), Connection.Connecting)

    private fun stream(server: ServerConfig): Flow<Connection> = flow {
        emit(Connection.Connecting)
        var lastState: ServerState? = null
        var failures = 0
        while (true) {
            try {
                api.events(server).collect { state ->
                    failures = 0
                    lastState = state
                    emit(Connection.Online(state))
                }
                error("The event stream ended")
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                val unauthorized = e is ApiException && e.isUnauthorized
                emit(Connection.Offline(unauthorized, e.message, lastState))
                if (unauthorized) {
                    // A wrong token does not fix itself: wait for new settings or a manual retry.
                    retryRequests.first()
                } else {
                    failures++
                    val delayMillis = minOf(30_000L, 1_000L shl minOf(failures, 5))
                    withTimeoutOrNull(delayMillis) { retryRequests.first() }
                }
            }
        }
    }

    fun retryNow() {
        retryRequests.tryEmit(Unit)
    }

    private suspend fun server(): ServerConfig =
        settingsStore.settings.first().server ?: throw NotConfiguredException()

    suspend fun submit(links: List<String>, options: JobOptions): SubmitResponse =
        api.submit(server(), links, options)

    suspend fun job(id: String): Job = api.job(server(), id)

    suspend fun cancel(id: String): Job = api.cancel(server(), id)

    suspend fun delete(id: String) = api.delete(server(), id)

    /** Checks a server that is not saved yet. */
    suspend fun test(server: ServerConfig): ServerInfo = api.info(server)
}
