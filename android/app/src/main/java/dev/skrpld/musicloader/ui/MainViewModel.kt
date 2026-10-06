package dev.skrpld.musicloader.ui

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import dev.skrpld.musicloader.data.AppSettings
import dev.skrpld.musicloader.data.Connection
import dev.skrpld.musicloader.data.ServerRepository
import dev.skrpld.musicloader.data.SettingsStore
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.stateIn

class MainViewModel(
    private val repository: ServerRepository,
    settingsStore: SettingsStore,
) : ViewModel() {
    /** Null until the stored settings are loaded. */
    val settings: StateFlow<AppSettings?> =
        settingsStore.settings.stateIn(viewModelScope, SharingStarted.Eagerly, null)

    val connection: StateFlow<Connection> = repository.connection

    fun retry() = repository.retryNow()
}
