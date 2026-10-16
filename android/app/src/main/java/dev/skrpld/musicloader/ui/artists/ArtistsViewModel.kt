package dev.skrpld.musicloader.ui.artists

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import dev.skrpld.musicloader.data.AccountAction
import dev.skrpld.musicloader.data.ArtistAccount
import dev.skrpld.musicloader.data.ServerRepository
import kotlin.coroutines.cancellation.CancellationException
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.receiveAsFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

data class ArtistsState(
    val accounts: List<ArtistAccount> = emptyList(),
    val loading: Boolean = true,
    /** Loading failed and nothing was loaded before. */
    val failed: Boolean = false,
    /** Accounts with an action on the way. */
    val busy: Set<String> = emptySet(),
)

/** The SoundCloud accounts of the library (server or phone) and the user's decisions on them. */
class ArtistsViewModel(private val repository: ServerRepository) : ViewModel() {
    private val _state = MutableStateFlow(ArtistsState())
    val state: StateFlow<ArtistsState> = _state.asStateFlow()

    private val _failures = Channel<String?>(Channel.BUFFERED)

    /** Messages of actions that failed, for a snackbar. */
    val failures: Flow<String?> = _failures.receiveAsFlow()

    fun refresh() {
        _state.update { it.copy(loading = true) }
        viewModelScope.launch {
            try {
                val accounts = repository.artists()
                _state.update { it.copy(accounts = accounts, loading = false, failed = false) }
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                _state.update { it.copy(loading = false, failed = it.accounts.isEmpty()) }
                _failures.trySend(e.message)
            }
        }
    }

    fun act(account: ArtistAccount, action: AccountAction, name: String? = null) {
        _state.update { it.copy(busy = it.busy + account.id) }
        viewModelScope.launch {
            try {
                val changed = repository.artistAction(account.id, action, name?.trim())
                // The server sorts the list; refresh to keep that order.
                val accounts = try {
                    repository.artists()
                } catch (e: CancellationException) {
                    throw e
                } catch (e: Exception) {
                    _state.value.accounts.map { if (it.id == changed.id) changed else it }
                }
                _state.update { it.copy(accounts = accounts, failed = false) }
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                _failures.trySend(e.message)
            } finally {
                _state.update { it.copy(busy = it.busy - account.id) }
            }
        }
    }
}
