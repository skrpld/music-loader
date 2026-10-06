package dev.skrpld.musicloader

import android.content.Intent
import android.graphics.Color
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.SystemBarStyle
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.viewModels
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import dev.skrpld.musicloader.data.ThemeMode
import dev.skrpld.musicloader.ui.MainViewModel
import dev.skrpld.musicloader.ui.MusicLoaderApp
import dev.skrpld.musicloader.ui.download.DownloadViewModel
import dev.skrpld.musicloader.ui.jobs.JobsViewModel
import dev.skrpld.musicloader.ui.settings.SettingsViewModel
import dev.skrpld.musicloader.ui.supportsDynamicColor
import dev.skrpld.musicloader.ui.theme.MusicLoaderTheme
import dev.skrpld.musicloader.ui.theme.isDark

class MainActivity : ComponentActivity() {
    private val factory get() = (application as MusicLoaderApplication).container.viewModelFactory

    private val mainViewModel: MainViewModel by viewModels { factory }
    private val downloadViewModel: DownloadViewModel by viewModels { factory }
    private val jobsViewModel: JobsViewModel by viewModels { factory }
    private val settingsViewModel: SettingsViewModel by viewModels { factory }

    override fun onCreate(savedInstanceState: Bundle?) {
        enableEdgeToEdge()
        super.onCreate(savedInstanceState)
        if (savedInstanceState == null) handleShare(intent)
        addOnNewIntentListener { handleShare(it) }

        setContent {
            val settings by mainViewModel.settings.collectAsStateWithLifecycle()
            val darkTheme = (settings?.themeMode ?: ThemeMode.System).isDark()
            // System bar icons follow the app theme, which may differ from the system one.
            DisposableEffect(darkTheme) {
                enableEdgeToEdge(
                    statusBarStyle = SystemBarStyle.auto(Color.TRANSPARENT, Color.TRANSPARENT) { darkTheme },
                    navigationBarStyle = SystemBarStyle.auto(Color.TRANSPARENT, Color.TRANSPARENT) { darkTheme },
                )
                onDispose {}
            }
            MusicLoaderTheme(darkTheme = darkTheme, dynamicColor = settings?.dynamicColor ?: true) {
                MusicLoaderApp(
                    mainViewModel = mainViewModel,
                    downloadViewModel = downloadViewModel,
                    jobsViewModel = jobsViewModel,
                    settingsViewModel = settingsViewModel,
                    appVersion = BuildConfig.VERSION_NAME,
                    dynamicColorAvailable = supportsDynamicColor,
                )
            }
        }
    }

    /** Links shared from Spotify, SoundCloud or a browser land in the download form. */
    private fun handleShare(intent: Intent?) {
        if (intent?.action != Intent.ACTION_SEND) return
        val text = intent.getStringExtra(Intent.EXTRA_TEXT) ?: return
        downloadViewModel.receiveShared(text)
    }
}
