package dev.skrpld.musicloader

import android.app.Application
import android.content.Context
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewmodel.initializer
import androidx.lifecycle.viewmodel.viewModelFactory
import dev.skrpld.musicloader.data.DataStoreSettings
import dev.skrpld.musicloader.data.MusicLoaderApi
import dev.skrpld.musicloader.data.ServerRepository
import dev.skrpld.musicloader.data.SettingsStore
import dev.skrpld.musicloader.engine.LocalEngine
import dev.skrpld.musicloader.ui.MainViewModel
import dev.skrpld.musicloader.ui.download.DownloadViewModel
import dev.skrpld.musicloader.ui.jobs.JobsViewModel
import dev.skrpld.musicloader.ui.settings.SettingsViewModel
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import okhttp3.OkHttpClient

class MusicLoaderApplication : Application() {
    val container: AppContainer by lazy { AppContainer(this) }
}

/** Manual dependency wiring: one settings store, HTTP client, downloader and repository per process. */
class AppContainer(context: Context) {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)

    private val httpClient = OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS)
        .readTimeout(30, TimeUnit.SECONDS)
        .writeTimeout(30, TimeUnit.SECONDS)
        .build()

    val settings: SettingsStore = DataStoreSettings(context)

    val localEngine = LocalEngine(context)

    val repository = ServerRepository(settings, MusicLoaderApi(httpClient), localEngine, scope)

    val viewModelFactory: ViewModelProvider.Factory = viewModelFactory {
        initializer { MainViewModel(repository, settings) }
        initializer { DownloadViewModel(repository, settings) }
        initializer { JobsViewModel(repository) }
        initializer { SettingsViewModel(repository, settings, localEngine) }
    }
}
