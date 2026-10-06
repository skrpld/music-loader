package dev.skrpld.musicloader.ui.download

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.consumeWindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AssistChip
import androidx.compose.material3.AssistChipDefaults
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
import androidx.compose.material3.LocalContentColor
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SegmentedListItem
import androidx.compose.material3.SnackbarDuration
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.SnackbarResult
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.ToggleButton
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.input.nestedscroll.nestedScroll
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.role
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import dev.skrpld.musicloader.R
import dev.skrpld.musicloader.data.Connection
import dev.skrpld.musicloader.data.LinkScan
import dev.skrpld.musicloader.data.LyricsMode
import dev.skrpld.musicloader.ui.components.ConnectionBanner
import dev.skrpld.musicloader.ui.components.Pill
import dev.skrpld.musicloader.ui.components.SectionHeader
import dev.skrpld.musicloader.ui.jobs.lyricsModeName
import dev.skrpld.musicloader.ui.rememberClipboardAccess
import kotlinx.coroutines.launch

private class DownloadMessages(
    val submitted: String,
    val submittedWithRejected: String,
    val open: String,
    val failed: String,
    val unauthorized: String,
    val notConfigured: String,
)

@OptIn(ExperimentalMaterial3Api::class, ExperimentalMaterial3ExpressiveApi::class)
@Composable
fun DownloadScreen(
    viewModel: DownloadViewModel,
    connection: Connection,
    onOpenSettings: () -> Unit,
    onOpenJob: (String) -> Unit,
    onRetry: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val scrollBehavior = TopAppBarDefaults.exitUntilCollapsedScrollBehavior()
    val snackbarHostState = remember { SnackbarHostState() }
    val options by viewModel.options.collectAsStateWithLifecycle()
    val clipboard = rememberClipboardAccess()
    val scope = rememberCoroutineScope()

    val messages by rememberUpdatedState(
        DownloadMessages(
            submitted = stringResource(R.string.download_submitted),
            submittedWithRejected = stringResource(R.string.download_submitted_rejected),
            open = stringResource(R.string.action_open),
            failed = stringResource(R.string.download_failed),
            unauthorized = stringResource(R.string.connection_unauthorized),
            notConfigured = stringResource(R.string.connection_not_configured),
        ),
    )
    val openJob by rememberUpdatedState(onOpenJob)
    LaunchedEffect(viewModel, snackbarHostState) {
        viewModel.events.collect { event ->
            when (event) {
                is DownloadEvent.Submitted -> {
                    val text = if (event.rejected > 0) {
                        messages.submittedWithRejected.format(event.rejected)
                    } else {
                        messages.submitted
                    }
                    val result = snackbarHostState.showSnackbar(
                        message = text,
                        actionLabel = messages.open,
                        withDismissAction = true,
                        duration = SnackbarDuration.Short,
                    )
                    if (result == SnackbarResult.ActionPerformed) openJob(event.jobId)
                }
                is DownloadEvent.Failed -> snackbarHostState.showSnackbar(
                    when {
                        event.notConfigured -> messages.notConfigured
                        event.unauthorized -> messages.unauthorized
                        else -> messages.failed.format(event.message.orEmpty())
                    },
                )
            }
        }
    }

    Scaffold(
        modifier = modifier.nestedScroll(scrollBehavior.nestedScrollConnection),
        topBar = {
            LargeFlexibleTopAppBar(
                title = { Text(stringResource(R.string.app_name)) },
                subtitle = { Text(connectionSubtitle(connection)) },
                scrollBehavior = scrollBehavior,
            )
        },
        snackbarHost = { SnackbarHost(snackbarHostState) },
    ) { padding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
                .consumeWindowInsets(padding)
                .imePadding()
                .verticalScroll(rememberScrollState())
                .padding(horizontal = 16.dp, vertical = 8.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            val contentModifier = Modifier.widthIn(max = 640.dp).fillMaxWidth()
            ConnectionBanner(
                connection = connection,
                onRetry = onRetry,
                onOpenSettings = onOpenSettings,
                modifier = contentModifier,
            )
            LinksCard(
                text = viewModel.linksText,
                scan = viewModel.scan,
                onTextChange = viewModel::updateLinks,
                onClear = viewModel::clearLinks,
                onPaste = {
                    scope.launch { clipboard.read()?.let(viewModel::appendText) }
                },
                modifier = contentModifier,
            )
            Column(modifier = contentModifier) {
                SectionHeader(stringResource(R.string.lyrics_title))
                LyricsModeSelector(selected = options.lyricsMode, onSelect = viewModel::setLyricsMode)
                Text(
                    text = stringResource(
                        when (options.lyricsMode) {
                            LyricsMode.Strict -> R.string.lyrics_strict_description
                            LyricsMode.Loose -> R.string.lyrics_loose_description
                            LyricsMode.Off -> R.string.lyrics_off_description
                        },
                    ),
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(horizontal = 4.dp, vertical = 8.dp),
                )
            }
            Column(modifier = contentModifier) {
                SectionHeader(stringResource(R.string.options_title))
                OptionsList(
                    recheck = viewModel.recheck,
                    reposts = options.soundcloudReposts,
                    likes = options.soundcloudLikes,
                    onRecheckChange = { viewModel.recheck = it },
                    onRepostsChange = viewModel::setSoundcloudReposts,
                    onLikesChange = viewModel::setSoundcloudLikes,
                )
            }
            Button(
                onClick = viewModel::submit,
                shapes = ButtonDefaults.shapes(),
                enabled = viewModel.scan.links.isNotEmpty() && !viewModel.submitting &&
                    connection != Connection.NotConfigured && connection != Connection.NeedsStorageAccess,
                contentPadding = ButtonDefaults.contentPaddingFor(ButtonDefaults.MediumContainerHeight),
                modifier = contentModifier
                    .padding(top = 8.dp, bottom = 24.dp)
                    .heightIn(min = ButtonDefaults.MediumContainerHeight),
            ) {
                if (viewModel.submitting) {
                    LoadingIndicator(
                        modifier = Modifier.size(ButtonDefaults.iconSizeFor(ButtonDefaults.MediumContainerHeight)),
                        color = LocalContentColor.current,
                    )
                } else {
                    Icon(
                        painter = painterResource(R.drawable.ic_playlist_add),
                        contentDescription = null,
                        modifier = Modifier.size(ButtonDefaults.iconSizeFor(ButtonDefaults.MediumContainerHeight)),
                    )
                }
                Spacer(Modifier.size(ButtonDefaults.iconSpacingFor(ButtonDefaults.MediumContainerHeight)))
                Text(
                    text = stringResource(R.string.download_submit),
                    style = ButtonDefaults.textStyleFor(ButtonDefaults.MediumContainerHeight),
                )
            }
        }
    }
}

@Composable
private fun connectionSubtitle(connection: Connection): String = stringResource(
    when (connection) {
        Connection.NotConfigured -> R.string.connection_not_configured
        Connection.NeedsStorageAccess -> R.string.settings_storage_needed
        Connection.Connecting -> R.string.connection_connecting
        Connection.Starting -> R.string.connection_starting
        is Connection.Offline -> if (connection.local) R.string.connection_local_failed else R.string.connection_offline
        is Connection.Online -> when {
            connection.state.busy -> R.string.connection_busy
            connection.local -> R.string.connection_local_ready
            else -> R.string.connection_online
        }
    },
)

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun LinksCard(
    text: String,
    scan: LinkScan,
    onTextChange: (String) -> Unit,
    onClear: () -> Unit,
    onPaste: () -> Unit,
    modifier: Modifier = Modifier,
) {
    Card(
        modifier = modifier,
        shape = MaterialTheme.shapes.extraLarge,
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceContainer),
    ) {
        Column(modifier = Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Text(text = stringResource(R.string.links_title), style = MaterialTheme.typography.titleMediumEmphasized)
            OutlinedTextField(
                value = text,
                onValueChange = onTextChange,
                modifier = Modifier.fillMaxWidth(),
                label = { Text(stringResource(R.string.links_label)) },
                placeholder = { Text(stringResource(R.string.links_placeholder)) },
                minLines = 4,
                maxLines = 12,
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri, autoCorrectEnabled = false),
                trailingIcon = if (text.isNotEmpty()) {
                    {
                        IconButton(onClick = onClear) {
                            Icon(
                                painter = painterResource(R.drawable.ic_close),
                                contentDescription = stringResource(R.string.action_clear),
                            )
                        }
                    }
                } else {
                    null
                },
                shape = MaterialTheme.shapes.large,
            )
            FlowRow(
                horizontalArrangement = Arrangement.spacedBy(8.dp),
                verticalArrangement = Arrangement.spacedBy(8.dp),
                itemVerticalAlignment = Alignment.CenterVertically,
            ) {
                AssistChip(
                    onClick = onPaste,
                    label = { Text(stringResource(R.string.action_paste)) },
                    leadingIcon = {
                        Icon(
                            painter = painterResource(R.drawable.ic_content_paste),
                            contentDescription = null,
                            modifier = Modifier.size(AssistChipDefaults.IconSize),
                        )
                    },
                )
                if (scan.spotify > 0) {
                    Pill(text = stringResource(R.string.links_spotify_count, scan.spotify), icon = R.drawable.ic_link)
                }
                if (scan.soundcloud > 0) {
                    Pill(text = stringResource(R.string.links_soundcloud_count, scan.soundcloud), icon = R.drawable.ic_link)
                }
                if (scan.unknown.isNotEmpty()) {
                    Pill(
                        text = stringResource(R.string.links_unknown_count, scan.unknown.size),
                        icon = R.drawable.ic_warning,
                        containerColor = MaterialTheme.colorScheme.errorContainer,
                        contentColor = MaterialTheme.colorScheme.onErrorContainer,
                    )
                }
            }
        }
    }
}

@OptIn(ExperimentalMaterial3ExpressiveApi::class)
@Composable
private fun LyricsModeSelector(selected: LyricsMode, onSelect: (LyricsMode) -> Unit) {
    val modes = LyricsMode.entries
    Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.spacedBy(ButtonGroupDefaults.ConnectedSpaceBetween),
    ) {
        modes.forEachIndexed { index, mode ->
            ToggleButton(
                checked = selected == mode,
                onCheckedChange = { onSelect(mode) },
                modifier = Modifier
                    .weight(1f)
                    .semantics { role = Role.RadioButton },
                shapes = when (index) {
                    0 -> ButtonGroupDefaults.connectedLeadingButtonShapes()
                    modes.lastIndex -> ButtonGroupDefaults.connectedTrailingButtonShapes()
                    else -> ButtonGroupDefaults.connectedMiddleButtonShapes()
                },
            ) {
                Text(text = lyricsModeName(mode), maxLines = 1)
            }
        }
    }
}

@OptIn(ExperimentalMaterial3ExpressiveApi::class)
@Composable
private fun OptionsList(
    recheck: Boolean,
    reposts: Boolean,
    likes: Boolean,
    onRecheckChange: (Boolean) -> Unit,
    onRepostsChange: (Boolean) -> Unit,
    onLikesChange: (Boolean) -> Unit,
) {
    Column(verticalArrangement = Arrangement.spacedBy(ListItemDefaults.SegmentedGap)) {
        OptionItem(
            index = 0,
            icon = R.drawable.ic_sync,
            title = stringResource(R.string.option_recheck),
            description = stringResource(R.string.option_recheck_description),
            checked = recheck,
            onCheckedChange = onRecheckChange,
        )
        OptionItem(
            index = 1,
            icon = R.drawable.ic_repeat,
            title = stringResource(R.string.option_reposts),
            description = stringResource(R.string.option_profile_only),
            checked = reposts,
            onCheckedChange = onRepostsChange,
        )
        OptionItem(
            index = 2,
            icon = R.drawable.ic_favorite,
            title = stringResource(R.string.option_likes),
            description = stringResource(R.string.option_profile_only),
            checked = likes,
            onCheckedChange = onLikesChange,
        )
    }
}

@OptIn(ExperimentalMaterial3ExpressiveApi::class)
@Composable
private fun OptionItem(
    index: Int,
    icon: Int,
    title: String,
    description: String,
    checked: Boolean,
    onCheckedChange: (Boolean) -> Unit,
) {
    SegmentedListItem(
        checked = checked,
        onCheckedChange = onCheckedChange,
        shapes = ListItemDefaults.segmentedShapes(index = index, count = 3),
        leadingContent = { Icon(painterResource(icon), contentDescription = null) },
        supportingContent = { Text(description) },
        trailingContent = { Switch(checked = checked, onCheckedChange = null) },
    ) {
        Text(title)
    }
}
