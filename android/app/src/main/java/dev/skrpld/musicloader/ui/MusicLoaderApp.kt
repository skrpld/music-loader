package dev.skrpld.musicloader.ui

import androidx.activity.compose.BackHandler
import androidx.compose.animation.AnimatedContent
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.togetherWith
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.Badge
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.adaptive.navigationsuite.NavigationSuiteItem
import androidx.compose.material3.adaptive.navigationsuite.NavigationSuiteScaffold
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.res.stringResource
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import dev.skrpld.musicloader.R
import dev.skrpld.musicloader.data.Features
import dev.skrpld.musicloader.data.JobStatus
import dev.skrpld.musicloader.data.supports
import dev.skrpld.musicloader.data.serverState
import dev.skrpld.musicloader.ui.artists.ArtistsScreen
import dev.skrpld.musicloader.ui.artists.ArtistsViewModel
import dev.skrpld.musicloader.ui.download.DownloadScreen
import dev.skrpld.musicloader.ui.download.DownloadViewModel
import dev.skrpld.musicloader.ui.jobs.JobsPane
import dev.skrpld.musicloader.ui.jobs.JobsViewModel
import dev.skrpld.musicloader.ui.settings.SettingsScreen
import dev.skrpld.musicloader.ui.settings.SettingsViewModel

enum class Destination(val label: Int, val icon: Int, val selectedIcon: Int) {
    Download(R.string.nav_download, R.drawable.ic_download, R.drawable.ic_download_filled),
    Jobs(R.string.nav_jobs, R.drawable.ic_queue_music, R.drawable.ic_queue_music_filled),
    Settings(R.string.nav_settings, R.drawable.ic_settings, R.drawable.ic_settings_filled),
}

/**
 * Root of the UI. The navigation suite turns into a bottom bar, a navigation rail or a
 * wide rail depending on the window size.
 */
@Composable
fun MusicLoaderApp(
    mainViewModel: MainViewModel,
    downloadViewModel: DownloadViewModel,
    jobsViewModel: JobsViewModel,
    settingsViewModel: SettingsViewModel,
    artistsViewModel: ArtistsViewModel,
    appVersion: String,
    dynamicColorAvailable: Boolean,
) {
    var destination by rememberSaveable { mutableStateOf(Destination.Download) }
    // Settings → Artists, shown in place of the settings while open.
    var artistsOpen by rememberSaveable { mutableStateOf(false) }
    val connection by mainViewModel.connection.collectAsStateWithLifecycle()
    val pendingJobs = connection.serverState?.jobs?.count {
        it.status == JobStatus.Running || it.status == JobStatus.Queued
    } ?: 0

    LaunchedEffect(downloadViewModel) {
        downloadViewModel.shareReceived.collect { destination = Destination.Download }
    }
    val artistsSupported = connection.supports(Features.Artists)
    val showArtists = artistsOpen && artistsSupported && destination == Destination.Settings
    BackHandler(enabled = destination != Destination.Download) {
        if (showArtists) artistsOpen = false else destination = Destination.Download
    }

    NavigationSuiteScaffold(
        navigationItems = {
            Destination.entries.forEach { item ->
                val selected = item == destination
                NavigationSuiteItem(
                    selected = selected,
                    onClick = {
                        if (item == Destination.Settings && destination == Destination.Settings) artistsOpen = false
                        destination = item
                    },
                    icon = {
                        Icon(
                            painter = painterResource(if (selected) item.selectedIcon else item.icon),
                            contentDescription = null,
                        )
                    },
                    label = { Text(stringResource(item.label)) },
                    badge = if (item == Destination.Jobs && pendingJobs > 0) {
                        { Badge { Text(pendingJobs.toString()) } }
                    } else {
                        null
                    },
                )
            }
        },
    ) {
        val motion = MaterialTheme.motionScheme
        AnimatedContent(
            targetState = destination,
            transitionSpec = { fadeIn(motion.defaultEffectsSpec()).togetherWith(fadeOut(motion.fastEffectsSpec())) },
            label = "destination",
        ) { current ->
            when (current) {
                Destination.Download -> DownloadScreen(
                    viewModel = downloadViewModel,
                    connection = connection,
                    onOpenSettings = { destination = Destination.Settings },
                    onOpenJob = { id ->
                        jobsViewModel.select(id)
                        destination = Destination.Jobs
                    },
                    onRetry = mainViewModel::retry,
                    modifier = Modifier.fillMaxSize(),
                )
                Destination.Jobs -> JobsPane(
                    viewModel = jobsViewModel,
                    onAddLinks = { destination = Destination.Download },
                    onOpenSettings = { destination = Destination.Settings },
                    modifier = Modifier.fillMaxSize(),
                )
                Destination.Settings -> if (showArtists) {
                    ArtistsScreen(
                        viewModel = artistsViewModel,
                        onBack = { artistsOpen = false },
                        modifier = Modifier.fillMaxSize(),
                    )
                } else {
                    SettingsScreen(
                        viewModel = settingsViewModel,
                        appVersion = appVersion,
                        dynamicColorAvailable = dynamicColorAvailable,
                        onOpenArtists = if (artistsSupported) {
                            { artistsOpen = true }
                        } else {
                            null
                        },
                        modifier = Modifier.fillMaxSize(),
                    )
                }
            }
        }
    }
}
