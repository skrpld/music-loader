package dev.skrpld.musicloader.ui.settings

import android.content.ActivityNotFoundException
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.consumeWindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.ButtonGroupDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExperimentalMaterial3ExpressiveApi
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LargeFlexibleTopAppBar
import androidx.compose.material3.ListItemDefaults
import androidx.compose.material3.LoadingIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SegmentedListItem
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.ToggleButton
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.input.nestedscroll.nestedScroll
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.res.pluralStringResource
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.role
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.unit.dp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LifecycleEventEffect
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import dev.skrpld.musicloader.R
import dev.skrpld.musicloader.data.AppSettings
import dev.skrpld.musicloader.data.DownloadMode
import dev.skrpld.musicloader.data.FeatPlacement
import dev.skrpld.musicloader.data.FilenameTemplate
import dev.skrpld.musicloader.data.JobOptions
import dev.skrpld.musicloader.data.LyricsProvider
import dev.skrpld.musicloader.data.LyricsRetry
import dev.skrpld.musicloader.data.LyricsTolerance
import dev.skrpld.musicloader.data.ServerUrls
import dev.skrpld.musicloader.data.ThemeMode
import dev.skrpld.musicloader.engine.StorageAccess
import dev.skrpld.musicloader.ui.components.SectionHeader

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SettingsScreen(
    viewModel: SettingsViewModel,
    appVersion: String,
    dynamicColorAvailable: Boolean,
    /** Opens Settings → Artists; null when the server has no artist decisions (older server). */
    onOpenArtists: (() -> Unit)?,
    /** Opens Settings → Check the library; null when the server cannot check (older server). */
    onOpenLibraryCheck: (() -> Unit)?,
    modifier: Modifier = Modifier,
) {
    val settings by viewModel.settings.collectAsStateWithLifecycle()
    val scrollBehavior = TopAppBarDefaults.exitUntilCollapsedScrollBehavior()
    Scaffold(
        modifier = modifier.nestedScroll(scrollBehavior.nestedScrollConnection),
        topBar = {
            LargeFlexibleTopAppBar(
                title = { Text(stringResource(R.string.nav_settings)) },
                scrollBehavior = scrollBehavior,
            )
        },
    ) { padding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
                .consumeWindowInsets(padding)
                .imePadding()
                .verticalScroll(rememberScrollState())
                .padding(horizontal = 16.dp, vertical = 8.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            val contentModifier = Modifier.widthIn(max = 640.dp).fillMaxWidth()
            Column(modifier = contentModifier, verticalArrangement = Arrangement.spacedBy(8.dp)) {
                val mode = settings?.mode ?: DownloadMode.Phone
                SectionHeader(stringResource(R.string.settings_mode))
                ModeSelector(selected = mode, onSelect = viewModel::setMode)
                Text(
                    text = stringResource(
                        when (mode) {
                            DownloadMode.Phone -> R.string.settings_mode_phone_description
                            DownloadMode.Server -> R.string.settings_mode_server_description
                        },
                    ),
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(horizontal = 4.dp),
                )
                when (mode) {
                    DownloadMode.Phone -> {
                        SectionHeader(stringResource(R.string.settings_phone))
                        PhoneCard(viewModel, settings)
                        SectionHeader(stringResource(R.string.settings_spotify))
                        SpotifyCard(viewModel)
                    }
                    DownloadMode.Server -> {
                        SectionHeader(stringResource(R.string.settings_server))
                        ServerCard(viewModel, settings)
                    }
                }
                SectionHeader(stringResource(R.string.settings_formatting))
                FormattingCard(viewModel, settings, serverMode = mode == DownloadMode.Server)
                SectionHeader(stringResource(R.string.settings_lyrics))
                LyricsCard(viewModel, settings, serverMode = mode == DownloadMode.Server)
                if (onOpenArtists != null || onOpenLibraryCheck != null) {
                    SectionHeader(stringResource(R.string.settings_library))
                    LibraryItems(onOpenArtists, onOpenLibraryCheck)
                }
                SectionHeader(stringResource(R.string.settings_appearance))
                Text(
                    text = stringResource(R.string.settings_theme),
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(horizontal = 4.dp),
                )
                ThemeSelector(
                    selected = settings?.themeMode ?: ThemeMode.System,
                    onSelect = viewModel::setThemeMode,
                )
                DynamicColorItem(
                    checked = (settings?.dynamicColor ?: true) && dynamicColorAvailable,
                    available = dynamicColorAvailable,
                    onCheckedChange = viewModel::setDynamicColor,
                )
                SectionHeader(stringResource(R.string.settings_about))
                AboutItems(appVersion, showServerHowto = mode == DownloadMode.Server)
            }
        }
    }
}

@OptIn(ExperimentalMaterial3ExpressiveApi::class)
@Composable
private fun ModeSelector(selected: DownloadMode, onSelect: (DownloadMode) -> Unit) {
    val modes = DownloadMode.entries
    Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.spacedBy(ButtonGroupDefaults.ConnectedSpaceBetween),
    ) {
        modes.forEachIndexed { index, mode ->
            val (label, icon) = when (mode) {
                DownloadMode.Phone -> R.string.mode_phone to R.drawable.ic_library_music
                DownloadMode.Server -> R.string.mode_server to R.drawable.ic_dns
            }
            ToggleButton(
                checked = selected == mode,
                onCheckedChange = { onSelect(mode) },
                modifier = Modifier
                    .weight(1f)
                    .semantics { role = Role.RadioButton },
                icon = { Icon(painterResource(icon), contentDescription = null, modifier = Modifier.size(18.dp)) },
                shapes = when (index) {
                    0 -> ButtonGroupDefaults.connectedLeadingButtonShapes()
                    modes.lastIndex -> ButtonGroupDefaults.connectedTrailingButtonShapes()
                    else -> ButtonGroupDefaults.connectedMiddleButtonShapes()
                },
            ) {
                Text(text = stringResource(label), maxLines = 1)
            }
        }
    }
}

@OptIn(ExperimentalMaterial3ExpressiveApi::class)
@Composable
private fun PhoneCard(viewModel: SettingsViewModel, settings: AppSettings?) {
    val context = LocalContext.current
    var pickFailed by rememberSaveable { mutableStateOf(false) }
    LifecycleEventEffect(Lifecycle.Event.ON_RESUME) { viewModel.refreshStorageAccess() }
    val permissionLauncher = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) {
        viewModel.refreshStorageAccess()
    }
    val settingsLauncher = rememberLauncherForActivityResult(ActivityResultContracts.StartActivityForResult()) {
        viewModel.refreshStorageAccess()
    }
    val folderPicker = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocumentTree()) { uri ->
        if (uri == null) return@rememberLauncherForActivityResult
        val path = StorageAccess.pathOf(uri)
        pickFailed = path == null
        if (path != null) viewModel.pickMusicDir(path)
    }

    Card(
        shape = MaterialTheme.shapes.extraLarge,
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceContainer),
    ) {
        Column(modifier = Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            if (viewModel.storageGranted) {
                ResultRow(
                    icon = R.drawable.ic_check_circle,
                    tint = MaterialTheme.colorScheme.primary,
                    title = stringResource(R.string.settings_storage_granted),
                    body = null,
                )
            } else {
                ResultRow(
                    icon = R.drawable.ic_warning,
                    tint = MaterialTheme.colorScheme.error,
                    title = stringResource(R.string.settings_storage_needed),
                    body = stringResource(R.string.settings_storage_needed_body),
                )
                Button(
                    onClick = {
                        if (StorageAccess.usesPermission) {
                            permissionLauncher.launch(StorageAccess.PERMISSION)
                        } else {
                            val intents = StorageAccess.settingsIntents(context)
                            try {
                                settingsLauncher.launch(intents[0])
                            } catch (e: ActivityNotFoundException) {
                                settingsLauncher.launch(intents[1])
                            }
                        }
                    },
                    shapes = ButtonDefaults.shapes(),
                    modifier = Modifier.align(Alignment.End),
                ) {
                    Text(stringResource(R.string.action_allow))
                }
            }
            val folderError = !viewModel.musicDirValid
            OutlinedTextField(
                value = viewModel.musicDirInput,
                onValueChange = {
                    pickFailed = false
                    viewModel.updateMusicDir(it)
                },
                modifier = Modifier.fillMaxWidth(),
                label = { Text(stringResource(R.string.settings_music_dir)) },
                placeholder = { Text(viewModel.defaultMusicDir) },
                leadingIcon = { Icon(painterResource(R.drawable.ic_folder), contentDescription = null) },
                trailingIcon = {
                    IconButton(onClick = { folderPicker.launch(null) }) {
                        Icon(
                            painterResource(R.drawable.ic_library_music),
                            contentDescription = stringResource(R.string.action_choose_folder),
                        )
                    }
                },
                supportingText = {
                    Text(
                        when {
                            pickFailed -> stringResource(R.string.settings_music_dir_unsupported)
                            folderError -> stringResource(R.string.settings_music_dir_invalid)
                            else -> stringResource(R.string.settings_music_dir_hint, viewModel.defaultMusicDir)
                        },
                    )
                },
                isError = folderError || pickFailed,
                singleLine = true,
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri, autoCorrectEnabled = false),
                shape = MaterialTheme.shapes.large,
            )
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(8.dp, Alignment.End),
            ) {
                OutlinedButton(onClick = { folderPicker.launch(null) }, shapes = ButtonDefaults.shapes()) {
                    Text(stringResource(R.string.action_choose_folder))
                }
                val saved = viewModel.isMusicDirSaved(settings)
                Button(
                    onClick = viewModel::saveMusicDir,
                    shapes = ButtonDefaults.shapes(),
                    enabled = !folderError && !saved,
                ) {
                    Text(stringResource(if (saved) R.string.action_saved else R.string.action_save))
                }
            }
        }
    }
}

@OptIn(ExperimentalMaterial3ExpressiveApi::class)
@Composable
private fun SpotifyCard(viewModel: SettingsViewModel) {
    val saved by viewModel.spotifyCredentials.collectAsStateWithLifecycle()
    var secretVisible by rememberSaveable { mutableStateOf(false) }
    val isSaved = viewModel.isSpotifySaved(saved)
    val pairError = !viewModel.spotifyValid
    Card(
        shape = MaterialTheme.shapes.extraLarge,
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceContainer),
    ) {
        Column(modifier = Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Text(
                text = stringResource(R.string.settings_spotify_description),
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            OutlinedTextField(
                value = viewModel.spotifyIdInput,
                onValueChange = viewModel::updateSpotifyId,
                modifier = Modifier.fillMaxWidth(),
                label = { Text(stringResource(R.string.settings_spotify_client_id)) },
                singleLine = true,
                isError = pairError,
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password, autoCorrectEnabled = false),
                shape = MaterialTheme.shapes.large,
            )
            OutlinedTextField(
                value = viewModel.spotifySecretInput,
                onValueChange = viewModel::updateSpotifySecret,
                modifier = Modifier.fillMaxWidth(),
                label = { Text(stringResource(R.string.settings_spotify_client_secret)) },
                supportingText = {
                    Text(
                        stringResource(
                            when {
                                viewModel.spotifySaveFailed -> R.string.settings_spotify_save_failed
                                pairError -> R.string.settings_spotify_pair_invalid
                                else -> R.string.settings_spotify_secret_hint
                            },
                        ),
                    )
                },
                isError = pairError || viewModel.spotifySaveFailed,
                singleLine = true,
                visualTransformation = if (secretVisible) VisualTransformation.None else PasswordVisualTransformation(),
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password, autoCorrectEnabled = false),
                trailingIcon = {
                    IconButton(onClick = { secretVisible = !secretVisible }) {
                        Icon(
                            painter = painterResource(
                                if (secretVisible) R.drawable.ic_visibility_off else R.drawable.ic_visibility,
                            ),
                            contentDescription = stringResource(
                                if (secretVisible) R.string.action_hide_secret else R.string.action_show_secret,
                            ),
                        )
                    }
                },
                shape = MaterialTheme.shapes.large,
            )
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(8.dp, Alignment.End),
            ) {
                OutlinedButton(
                    onClick = viewModel::clearSpotify,
                    shapes = ButtonDefaults.shapes(),
                    enabled = saved?.isSet == true || viewModel.spotifyIdInput.isNotBlank() ||
                        viewModel.spotifySecretInput.isNotBlank(),
                ) {
                    Text(stringResource(R.string.action_clear))
                }
                Button(
                    onClick = viewModel::saveSpotify,
                    shapes = ButtonDefaults.shapes(),
                    enabled = !pairError && !isSaved,
                ) {
                    Text(stringResource(if (isSaved) R.string.action_saved else R.string.action_save))
                }
            }
        }
    }
}

@OptIn(ExperimentalMaterial3ExpressiveApi::class)
@Composable
private fun FormattingCard(viewModel: SettingsViewModel, settings: AppSettings?, serverMode: Boolean) {
    val options = settings?.jobOptions ?: JobOptions()
    Card(
        shape = MaterialTheme.shapes.extraLarge,
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceContainer),
    ) {
        Column(modifier = Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Text(
                text = stringResource(
                    if (serverMode) R.string.settings_formatting_description_server else R.string.settings_formatting_description,
                ),
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Text(
                text = stringResource(R.string.settings_feat),
                style = MaterialTheme.typography.bodyMedium,
            )
            FeatSelector(selected = options.featPlacement, onSelect = viewModel::setFeatPlacement)
            Text(
                text = stringResource(
                    when (options.featPlacement) {
                        FeatPlacement.Both -> R.string.settings_feat_both_hint
                        FeatPlacement.Artist -> R.string.settings_feat_artist_hint
                        FeatPlacement.Title -> R.string.settings_feat_title_hint
                    },
                ),
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Column(verticalArrangement = Arrangement.spacedBy(ListItemDefaults.SegmentedGap)) {
                SwitchItem(
                    index = 0,
                    count = 3,
                    checked = options.soundcloudKeepVersions,
                    onCheckedChange = viewModel::setKeepVersions,
                    title = stringResource(R.string.settings_keep_versions),
                    hint = stringResource(R.string.settings_keep_versions_hint),
                )
                SwitchItem(
                    index = 1,
                    count = 3,
                    checked = options.soundcloudKeepProducers,
                    onCheckedChange = viewModel::setKeepProducers,
                    title = stringResource(R.string.settings_keep_producers),
                    hint = stringResource(R.string.settings_keep_producers_hint),
                )
                SwitchItem(
                    index = 2,
                    count = 3,
                    checked = options.soundcloudStripPromo,
                    onCheckedChange = viewModel::setStripPromo,
                    title = stringResource(R.string.settings_strip_promo),
                    hint = stringResource(R.string.settings_strip_promo_hint),
                )
            }
            val templateError = !viewModel.filenameValid
            OutlinedTextField(
                value = viewModel.filenameInput,
                onValueChange = viewModel::updateFilename,
                modifier = Modifier.fillMaxWidth(),
                label = { Text(stringResource(R.string.settings_filename)) },
                supportingText = {
                    Text(
                        stringResource(
                            if (templateError) R.string.settings_filename_invalid else R.string.settings_filename_hint,
                        ),
                    )
                },
                isError = templateError,
                singleLine = true,
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Ascii, autoCorrectEnabled = false),
                shape = MaterialTheme.shapes.large,
            )
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(8.dp, Alignment.End),
            ) {
                val saved = viewModel.isFilenameSaved(settings)
                OutlinedButton(
                    onClick = viewModel::resetFilename,
                    shapes = ButtonDefaults.shapes(),
                    enabled = viewModel.filenameInput.trim() != FilenameTemplate.DEFAULT || !saved,
                ) {
                    Text(stringResource(R.string.action_reset))
                }
                Button(
                    onClick = viewModel::saveFilename,
                    shapes = ButtonDefaults.shapes(),
                    enabled = !templateError && !saved,
                ) {
                    Text(stringResource(if (saved) R.string.action_saved else R.string.action_save))
                }
            }
        }
    }
}

@OptIn(ExperimentalMaterial3ExpressiveApi::class)
@Composable
private fun LyricsCard(viewModel: SettingsViewModel, settings: AppSettings?, serverMode: Boolean) {
    val options = settings?.jobOptions ?: JobOptions()
    val enabled = options.providers
    // Providers that are on come first, in the order they are asked.
    val rows = enabled + LyricsProvider.entries.filter { it !in enabled }
    Card(
        shape = MaterialTheme.shapes.extraLarge,
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceContainer),
    ) {
        Column(modifier = Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Text(
                text = stringResource(
                    if (serverMode) R.string.settings_lyrics_description_server else R.string.settings_lyrics_description,
                ),
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Text(text = stringResource(R.string.settings_lyrics_providers), style = MaterialTheme.typography.bodyMedium)
            Column(verticalArrangement = Arrangement.spacedBy(ListItemDefaults.SegmentedGap)) {
                rows.forEachIndexed { index, provider ->
                    val on = provider in enabled
                    val position = enabled.indexOf(provider)
                    SegmentedListItem(
                        checked = on,
                        onCheckedChange = { viewModel.setLyricsProvider(provider, it) },
                        shapes = ListItemDefaults.segmentedShapes(index = index, count = rows.size),
                        supportingContent = {
                            Text(
                                when {
                                    !on -> stringResource(R.string.settings_lyrics_provider_off)
                                    enabled.size == 1 -> stringResource(R.string.settings_lyrics_provider_only)
                                    else -> stringResource(R.string.settings_lyrics_provider_position, position + 1)
                                },
                            )
                        },
                        trailingContent = {
                            Row(verticalAlignment = Alignment.CenterVertically) {
                                if (on && position > 0) {
                                    IconButton(onClick = { viewModel.moveLyricsProviderUp(provider) }) {
                                        Icon(
                                            painterResource(R.drawable.ic_arrow_upward),
                                            contentDescription = stringResource(
                                                R.string.settings_lyrics_move_up,
                                                provider.label,
                                            ),
                                        )
                                    }
                                }
                                Switch(checked = on, onCheckedChange = null)
                            }
                        },
                    ) {
                        Text(provider.label)
                    }
                }
            }
            Column(verticalArrangement = Arrangement.spacedBy(ListItemDefaults.SegmentedGap)) {
                SwitchItem(
                    index = 0,
                    count = 2,
                    checked = options.lyricsPlain,
                    onCheckedChange = viewModel::setLyricsPlain,
                    title = stringResource(R.string.settings_lyrics_plain),
                    hint = stringResource(R.string.settings_lyrics_plain_hint),
                )
                SwitchItem(
                    index = 1,
                    count = 2,
                    checked = options.lyricsOriginalForVersions,
                    onCheckedChange = viewModel::setLyricsOriginalForVersions,
                    title = stringResource(R.string.settings_lyrics_versions),
                    hint = stringResource(R.string.settings_lyrics_versions_hint),
                )
            }
            Text(text = stringResource(R.string.settings_lyrics_tolerance), style = MaterialTheme.typography.bodyMedium)
            ChoiceSelector(
                choices = LyricsTolerance.choices,
                selected = options.lyricsLengthTolerance,
                label = { stringResource(R.string.settings_lyrics_seconds, it.toInt()) },
                onSelect = viewModel::setLyricsLengthTolerance,
            )
            Text(
                text = stringResource(R.string.settings_lyrics_tolerance_hint),
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Text(text = stringResource(R.string.settings_lyrics_retry), style = MaterialTheme.typography.bodyMedium)
            ChoiceSelector(
                choices = LyricsRetry.choices,
                selected = options.lyricsRetryDays,
                label = {
                    if (it == 0) {
                        stringResource(R.string.settings_lyrics_retry_always)
                    } else {
                        pluralStringResource(R.plurals.settings_lyrics_days, it, it)
                    }
                },
                onSelect = viewModel::setLyricsRetryDays,
            )
            Text(
                text = stringResource(R.string.settings_lyrics_retry_hint),
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}

@OptIn(ExperimentalMaterial3ExpressiveApi::class)
@Composable
private fun <T> ChoiceSelector(
    choices: List<T>,
    selected: T,
    label: @Composable (T) -> String,
    onSelect: (T) -> Unit,
) {
    Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.spacedBy(ButtonGroupDefaults.ConnectedSpaceBetween),
    ) {
        choices.forEachIndexed { index, choice ->
            ToggleButton(
                checked = selected == choice,
                onCheckedChange = { onSelect(choice) },
                modifier = Modifier
                    .weight(1f)
                    .semantics { role = Role.RadioButton },
                shapes = when (index) {
                    0 -> ButtonGroupDefaults.connectedLeadingButtonShapes()
                    choices.lastIndex -> ButtonGroupDefaults.connectedTrailingButtonShapes()
                    else -> ButtonGroupDefaults.connectedMiddleButtonShapes()
                },
            ) {
                Text(text = label(choice), maxLines = 1)
            }
        }
    }
}

@OptIn(ExperimentalMaterial3ExpressiveApi::class)
@Composable
private fun FeatSelector(selected: FeatPlacement, onSelect: (FeatPlacement) -> Unit) {
    val placements = FeatPlacement.entries
    Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.spacedBy(ButtonGroupDefaults.ConnectedSpaceBetween),
    ) {
        placements.forEachIndexed { index, placement ->
            val label = when (placement) {
                FeatPlacement.Both -> R.string.settings_feat_both
                FeatPlacement.Artist -> R.string.settings_feat_artist
                FeatPlacement.Title -> R.string.settings_feat_title
            }
            ToggleButton(
                checked = selected == placement,
                onCheckedChange = { onSelect(placement) },
                modifier = Modifier
                    .weight(1f)
                    .semantics { role = Role.RadioButton },
                shapes = when (index) {
                    0 -> ButtonGroupDefaults.connectedLeadingButtonShapes()
                    placements.lastIndex -> ButtonGroupDefaults.connectedTrailingButtonShapes()
                    else -> ButtonGroupDefaults.connectedMiddleButtonShapes()
                },
            ) {
                Text(text = stringResource(label), maxLines = 1)
            }
        }
    }
}

@OptIn(ExperimentalMaterial3ExpressiveApi::class)
@Composable
private fun SwitchItem(
    index: Int,
    count: Int,
    checked: Boolean,
    onCheckedChange: (Boolean) -> Unit,
    title: String,
    hint: String,
) {
    SegmentedListItem(
        checked = checked,
        onCheckedChange = onCheckedChange,
        shapes = ListItemDefaults.segmentedShapes(index = index, count = count),
        supportingContent = { Text(hint) },
        trailingContent = { Switch(checked = checked, onCheckedChange = null) },
    ) {
        Text(title)
    }
}

@OptIn(ExperimentalMaterial3ExpressiveApi::class)
@Composable
private fun ServerCard(viewModel: SettingsViewModel, settings: AppSettings?) {
    var tokenVisible by rememberSaveable { mutableStateOf(false) }
    val url = viewModel.normalizedUrl
    Card(
        shape = MaterialTheme.shapes.extraLarge,
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceContainer),
    ) {
        Column(modifier = Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            val urlError = viewModel.urlInput.isNotBlank() && url == null
            OutlinedTextField(
                value = viewModel.urlInput,
                onValueChange = viewModel::updateUrl,
                modifier = Modifier.fillMaxWidth(),
                label = { Text(stringResource(R.string.settings_server_url)) },
                placeholder = { Text(stringResource(R.string.settings_server_url_placeholder)) },
                leadingIcon = { Icon(painterResource(R.drawable.ic_dns), contentDescription = null) },
                supportingText = {
                    Text(
                        stringResource(
                            if (urlError) R.string.settings_server_url_invalid else R.string.settings_server_url_hint,
                        ),
                    )
                },
                isError = urlError,
                singleLine = true,
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri, autoCorrectEnabled = false),
                shape = MaterialTheme.shapes.large,
            )
            val tokenError = viewModel.tokenInput.isNotBlank() && !viewModel.tokenValid
            OutlinedTextField(
                value = viewModel.tokenInput,
                onValueChange = viewModel::updateToken,
                modifier = Modifier.fillMaxWidth(),
                label = { Text(stringResource(R.string.settings_token)) },
                supportingText = {
                    Text(
                        stringResource(
                            if (tokenError) R.string.settings_token_invalid else R.string.settings_token_hint,
                        ),
                    )
                },
                isError = tokenError,
                singleLine = true,
                visualTransformation = if (tokenVisible) VisualTransformation.None else PasswordVisualTransformation(),
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password, autoCorrectEnabled = false),
                trailingIcon = {
                    IconButton(onClick = { tokenVisible = !tokenVisible }) {
                        Icon(
                            painter = painterResource(
                                if (tokenVisible) R.drawable.ic_visibility_off else R.drawable.ic_visibility,
                            ),
                            contentDescription = stringResource(
                                if (tokenVisible) R.string.action_hide_token else R.string.action_show_token,
                            ),
                        )
                    }
                },
                shape = MaterialTheme.shapes.large,
            )
            if (url != null && ServerUrls.isCleartext(url)) {
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Icon(
                        painter = painterResource(R.drawable.ic_warning),
                        contentDescription = null,
                        tint = MaterialTheme.colorScheme.tertiary,
                        modifier = Modifier.size(20.dp),
                    )
                    Text(
                        text = stringResource(R.string.settings_cleartext_warning),
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
            TestResult(viewModel.test)
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(8.dp, Alignment.End),
            ) {
                OutlinedButton(
                    onClick = viewModel::testConnection,
                    shapes = ButtonDefaults.shapes(),
                    enabled = viewModel.canSubmit && viewModel.test != ConnectionTest.Running,
                ) {
                    Text(stringResource(R.string.action_test))
                }
                Button(
                    onClick = viewModel::save,
                    shapes = ButtonDefaults.shapes(),
                    enabled = viewModel.canSubmit && !viewModel.isSaved(settings),
                ) {
                    Text(stringResource(if (viewModel.isSaved(settings)) R.string.action_saved else R.string.action_save))
                }
            }
        }
    }
}

@OptIn(ExperimentalMaterial3ExpressiveApi::class)
@Composable
private fun TestResult(test: ConnectionTest) {
    when (test) {
        ConnectionTest.Idle -> Unit
        ConnectionTest.Running -> Row(
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            LoadingIndicator(modifier = Modifier.size(32.dp))
            Text(stringResource(R.string.settings_testing), style = MaterialTheme.typography.bodyMedium)
        }
        is ConnectionTest.Success -> ResultRow(
            icon = R.drawable.ic_check_circle,
            tint = MaterialTheme.colorScheme.primary,
            title = stringResource(R.string.settings_test_ok, test.info.version),
            body = stringResource(R.string.settings_test_library, test.info.musicDir),
        )
        is ConnectionTest.Failure -> ResultRow(
            icon = R.drawable.ic_error,
            tint = MaterialTheme.colorScheme.error,
            title = stringResource(
                if (test.unauthorized) R.string.connection_unauthorized else R.string.settings_test_failed,
            ),
            body = if (test.unauthorized) null else test.message,
        )
    }
}

@Composable
private fun ResultRow(icon: Int, tint: androidx.compose.ui.graphics.Color, title: String, body: String?) {
    Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
        Icon(painterResource(icon), contentDescription = null, tint = tint)
        Column {
            Text(title, style = MaterialTheme.typography.bodyMedium)
            if (body != null) {
                Text(body, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
    }
}

@OptIn(ExperimentalMaterial3ExpressiveApi::class)
@Composable
private fun ThemeSelector(selected: ThemeMode, onSelect: (ThemeMode) -> Unit) {
    val modes = ThemeMode.entries
    Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.spacedBy(ButtonGroupDefaults.ConnectedSpaceBetween),
    ) {
        modes.forEachIndexed { index, mode ->
            val (label, icon) = when (mode) {
                ThemeMode.System -> R.string.theme_system to R.drawable.ic_brightness_auto
                ThemeMode.Light -> R.string.theme_light to R.drawable.ic_light_mode
                ThemeMode.Dark -> R.string.theme_dark to R.drawable.ic_dark_mode
            }
            ToggleButton(
                checked = selected == mode,
                onCheckedChange = { onSelect(mode) },
                modifier = Modifier
                    .weight(1f)
                    .semantics { role = Role.RadioButton },
                icon = { Icon(painterResource(icon), contentDescription = null, modifier = Modifier.size(18.dp)) },
                shapes = when (index) {
                    0 -> ButtonGroupDefaults.connectedLeadingButtonShapes()
                    modes.lastIndex -> ButtonGroupDefaults.connectedTrailingButtonShapes()
                    else -> ButtonGroupDefaults.connectedMiddleButtonShapes()
                },
            ) {
                Text(text = stringResource(label), maxLines = 1)
            }
        }
    }
}

@OptIn(ExperimentalMaterial3ExpressiveApi::class)
@Composable
private fun DynamicColorItem(checked: Boolean, available: Boolean, onCheckedChange: (Boolean) -> Unit) {
    SegmentedListItem(
        checked = checked,
        onCheckedChange = onCheckedChange,
        shapes = ListItemDefaults.segmentedShapes(index = 0, count = 1),
        enabled = available,
        modifier = Modifier.padding(top = 8.dp),
        leadingContent = { Icon(painterResource(R.drawable.ic_palette), contentDescription = null) },
        supportingContent = {
            Text(
                stringResource(
                    if (available) R.string.settings_dynamic_color_hint else R.string.settings_dynamic_color_unavailable,
                ),
            )
        },
        trailingContent = { Switch(checked = checked, onCheckedChange = null, enabled = available) },
    ) {
        Text(stringResource(R.string.settings_dynamic_color))
    }
}

/** Settings → Artists and Settings → Check the library, each only when the server has it. */
@OptIn(ExperimentalMaterial3ExpressiveApi::class)
@Composable
private fun LibraryItems(onOpenArtists: (() -> Unit)?, onOpenLibraryCheck: (() -> Unit)?) {
    val count = listOfNotNull(onOpenArtists, onOpenLibraryCheck).size
    Column(verticalArrangement = Arrangement.spacedBy(ListItemDefaults.SegmentedGap)) {
        if (onOpenArtists != null) {
            NavigationItem(
                icon = R.drawable.ic_person,
                title = R.string.settings_artists,
                hint = R.string.settings_artists_hint,
                index = 0,
                count = count,
                onClick = onOpenArtists,
            )
        }
        if (onOpenLibraryCheck != null) {
            NavigationItem(
                icon = R.drawable.ic_search,
                title = R.string.check_title,
                hint = R.string.check_settings_hint,
                index = count - 1,
                count = count,
                onClick = onOpenLibraryCheck,
            )
        }
    }
}

@OptIn(ExperimentalMaterial3ExpressiveApi::class)
@Composable
private fun NavigationItem(icon: Int, title: Int, hint: Int, index: Int, count: Int, onClick: () -> Unit) {
    SegmentedListItem(
        onClick = onClick,
        shapes = ListItemDefaults.segmentedShapes(index = index, count = count),
        leadingContent = { Icon(painterResource(icon), contentDescription = null) },
        supportingContent = { Text(stringResource(hint)) },
        trailingContent = { Icon(painterResource(R.drawable.ic_chevron_right), contentDescription = null) },
    ) {
        Text(stringResource(title))
    }
}

@OptIn(ExperimentalMaterial3ExpressiveApi::class)
@Composable
private fun AboutItems(appVersion: String, showServerHowto: Boolean) {
    val count = if (showServerHowto) 2 else 1
    Column(verticalArrangement = Arrangement.spacedBy(ListItemDefaults.SegmentedGap)) {
        SegmentedListItem(
            shapes = ListItemDefaults.segmentedShapes(index = 0, count = count),
            leadingContent = { Icon(painterResource(R.drawable.ic_info), contentDescription = null) },
            supportingContent = { Text(stringResource(R.string.settings_version, appVersion)) },
        ) {
            Text(stringResource(R.string.app_name))
        }
        if (!showServerHowto) return@Column
        SegmentedListItem(
            shapes = ListItemDefaults.segmentedShapes(index = 1, count = count),
            leadingContent = { Icon(painterResource(R.drawable.ic_dns), contentDescription = null) },
            supportingContent = { Text(stringResource(R.string.settings_server_command)) },
        ) {
            Text(stringResource(R.string.settings_server_howto))
        }
    }
}
