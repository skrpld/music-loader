package dev.skrpld.musicloader.ui.artists

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExperimentalMaterial3ExpressiveApi
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.ListItemDefaults
import androidx.compose.material3.LoadingIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SegmentedListItem
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.input.nestedscroll.nestedScroll
import androidx.compose.ui.platform.LocalUriHandler
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.res.pluralStringResource
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import dev.skrpld.musicloader.R
import dev.skrpld.musicloader.data.AccountAction
import dev.skrpld.musicloader.data.AccountStatus
import dev.skrpld.musicloader.data.ArtistAccount
import dev.skrpld.musicloader.ui.components.CenteredLoading
import dev.skrpld.musicloader.ui.components.EmptyState
import dev.skrpld.musicloader.ui.components.SectionHeader

/**
 * The SoundCloud accounts of the library and the artist name their tracks get:
 * suggestions to accept or reject, and every account to rename, keep, mark as a
 * reupload account or unlock (the same actions as `music-loader artists`).
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ArtistsScreen(
    viewModel: ArtistsViewModel,
    onBack: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val state by viewModel.state.collectAsStateWithLifecycle()
    val snackbarHostState = remember { SnackbarHostState() }
    val failedFormat = stringResource(R.string.action_failed)
    var editingId by rememberSaveable { mutableStateOf<String?>(null) }

    LaunchedEffect(viewModel) { viewModel.refresh() }
    LaunchedEffect(viewModel, snackbarHostState) {
        viewModel.failures.collect { message -> snackbarHostState.showSnackbar(failedFormat.format(message.orEmpty())) }
    }

    val scrollBehavior = TopAppBarDefaults.pinnedScrollBehavior()
    Scaffold(
        modifier = modifier.nestedScroll(scrollBehavior.nestedScrollConnection),
        snackbarHost = { SnackbarHost(snackbarHostState) },
        topBar = {
            TopAppBar(
                title = { Text(stringResource(R.string.settings_artists)) },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(
                            painter = painterResource(R.drawable.ic_arrow_back),
                            contentDescription = stringResource(R.string.action_back),
                        )
                    }
                },
                actions = {
                    IconButton(onClick = viewModel::refresh, enabled = !state.loading) {
                        Icon(
                            painter = painterResource(R.drawable.ic_refresh),
                            contentDescription = stringResource(R.string.action_retry),
                        )
                    }
                },
                scrollBehavior = scrollBehavior,
            )
        },
    ) { padding ->
        when {
            state.accounts.isEmpty() && state.loading -> CenteredLoading(Modifier.padding(padding))
            state.accounts.isEmpty() -> EmptyState(
                icon = if (state.failed) R.drawable.ic_error else R.drawable.ic_person,
                title = stringResource(if (state.failed) R.string.artists_load_failed else R.string.artists_empty_title),
                body = if (state.failed) null else stringResource(R.string.artists_empty_body),
                modifier = Modifier.padding(padding),
                action = if (state.failed) {
                    { Button(onClick = viewModel::refresh) { Text(stringResource(R.string.action_retry)) } }
                } else {
                    null
                },
            )
            else -> AccountList(
                state = state,
                contentPadding = padding,
                onAccept = { viewModel.act(it, AccountAction.Accept) },
                onReject = { viewModel.act(it, AccountAction.Reject) },
                onEdit = { editingId = it.id },
            )
        }
    }

    val editing = state.accounts.firstOrNull { it.id == editingId }
    if (editing != null) {
        EditAccountDialog(
            account = editing,
            onDismiss = { editingId = null },
            onAction = { action, name ->
                viewModel.act(editing, action, name)
                editingId = null
            },
        )
    }
}

@OptIn(ExperimentalMaterial3ExpressiveApi::class)
@Composable
private fun AccountList(
    state: ArtistsState,
    contentPadding: PaddingValues,
    onAccept: (ArtistAccount) -> Unit,
    onReject: (ArtistAccount) -> Unit,
    onEdit: (ArtistAccount) -> Unit,
) {
    val waiting = state.accounts.filter { it.status == AccountStatus.Suggestion }
    val others = state.accounts.filter { it.status != AccountStatus.Suggestion }
    LazyColumn(
        modifier = Modifier.fillMaxSize(),
        contentPadding = PaddingValues(
            start = 16.dp,
            end = 16.dp,
            top = contentPadding.calculateTopPadding() + 8.dp,
            bottom = contentPadding.calculateBottomPadding() + 24.dp,
        ),
        verticalArrangement = Arrangement.spacedBy(ListItemDefaults.SegmentedGap),
    ) {
        item(key = "description") {
            Text(
                text = stringResource(R.string.artists_description),
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(horizontal = 4.dp, vertical = 4.dp),
            )
        }
        if (waiting.isNotEmpty()) {
            item(key = "waiting-header") { SectionHeader(stringResource(R.string.artists_waiting)) }
            itemsIndexed(waiting, key = { _, account -> "waiting-${account.id}" }) { index, account ->
                AccountItem(
                    account = account,
                    index = index,
                    count = waiting.size,
                    busy = account.id in state.busy,
                    onClick = { onEdit(account) },
                    actions = {
                        Row(
                            horizontalArrangement = Arrangement.spacedBy(8.dp),
                            modifier = Modifier.padding(top = 8.dp),
                        ) {
                            Button(onClick = { onAccept(account) }, enabled = account.id !in state.busy) {
                                Text(stringResource(R.string.action_accept))
                            }
                            OutlinedButton(onClick = { onReject(account) }, enabled = account.id !in state.busy) {
                                Text(stringResource(R.string.action_reject))
                            }
                        }
                    },
                )
            }
        }
        if (others.isNotEmpty()) {
            item(key = "accounts-header") { SectionHeader(stringResource(R.string.artists_accounts)) }
            itemsIndexed(others, key = { _, account -> "account-${account.id}" }) { index, account ->
                AccountItem(
                    account = account,
                    index = index,
                    count = others.size,
                    busy = account.id in state.busy,
                    onClick = { onEdit(account) },
                )
            }
        }
    }
}

@OptIn(ExperimentalMaterial3ExpressiveApi::class)
@Composable
private fun AccountItem(
    account: ArtistAccount,
    index: Int,
    count: Int,
    busy: Boolean,
    onClick: () -> Unit,
    actions: (@Composable () -> Unit)? = null,
) {
    SegmentedListItem(
        onClick = onClick,
        shapes = ListItemDefaults.segmentedShapes(index = index, count = count),
        leadingContent = { Icon(painterResource(statusIcon(account.status)), contentDescription = null) },
        supportingContent = {
            Column {
                Text(statusText(account))
                if (account.tracks > 0) {
                    Text(pluralStringResource(R.plurals.artists_tracks, account.tracks, account.tracks))
                }
                actions?.invoke()
            }
        },
        trailingContent = when {
            busy -> {
                { LoadingIndicator(modifier = Modifier.size(24.dp)) }
            }
            account.locked -> {
                {
                    Icon(
                        painter = painterResource(R.drawable.ic_lock),
                        contentDescription = stringResource(R.string.artists_locked),
                    )
                }
            }
            else -> null
        },
    ) {
        Text(text = account.name, maxLines = 1, overflow = TextOverflow.Ellipsis)
    }
}

@Composable
private fun EditAccountDialog(
    account: ArtistAccount,
    onDismiss: () -> Unit,
    onAction: (AccountAction, String?) -> Unit,
) {
    var name by rememberSaveable(account.id) {
        mutableStateOf(account.alias ?: account.suggestion ?: account.handle)
    }
    val uriHandler = LocalUriHandler.current
    AlertDialog(
        onDismissRequest = onDismiss,
        icon = { Icon(painterResource(statusIcon(account.status)), contentDescription = null) },
        title = { Text(text = account.name, maxLines = 2, overflow = TextOverflow.Ellipsis) },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text(statusText(account), style = MaterialTheme.typography.bodyMedium)
                OutlinedTextField(
                    value = name,
                    onValueChange = { name = it.take(200) },
                    label = { Text(stringResource(R.string.artists_name)) },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                )
                Button(
                    onClick = { onAction(AccountAction.SetName, name) },
                    enabled = name.isNotBlank(),
                    modifier = Modifier.fillMaxWidth(),
                ) {
                    Text(stringResource(R.string.artists_set))
                }
                if (account.status != AccountStatus.Nick || !account.locked) {
                    OutlinedButton(
                        onClick = { onAction(AccountAction.KeepNick, null) },
                        modifier = Modifier.fillMaxWidth(),
                    ) {
                        Text(stringResource(R.string.artists_keep))
                    }
                }
                if (account.status != AccountStatus.Reuploader) {
                    OutlinedButton(
                        onClick = { onAction(AccountAction.Reuploader, null) },
                        modifier = Modifier.fillMaxWidth(),
                    ) {
                        Icon(
                            painter = painterResource(R.drawable.ic_repeat),
                            contentDescription = null,
                            modifier = Modifier.size(18.dp),
                        )
                        Text(stringResource(R.string.artists_reuploader), modifier = Modifier.padding(start = 8.dp))
                    }
                }
                if (account.locked) {
                    OutlinedButton(
                        onClick = { onAction(AccountAction.Unlock, null) },
                        modifier = Modifier.fillMaxWidth(),
                    ) {
                        Text(stringResource(R.string.artists_unlock))
                    }
                    Text(
                        text = stringResource(R.string.artists_unlock_hint),
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.align(Alignment.CenterHorizontally),
                    )
                }
            }
        },
        confirmButton = {
            TextButton(onClick = onDismiss) { Text(stringResource(R.string.action_close)) }
        },
        dismissButton = if (account.url.isNotBlank()) {
            {
                TextButton(onClick = { runCatching { uriHandler.openUri(account.url) } }) {
                    Text(stringResource(R.string.artists_open_profile))
                }
            }
        } else {
            null
        },
    )
}

private fun statusIcon(status: AccountStatus): Int = when (status) {
    AccountStatus.Confirmed -> R.drawable.ic_check_circle
    AccountStatus.Suggestion -> R.drawable.ic_lightbulb
    AccountStatus.Reuploader -> R.drawable.ic_repeat
    AccountStatus.NotChecked -> R.drawable.ic_cloud_off
    AccountStatus.Nick -> R.drawable.ic_person
}

@Composable
private fun statusText(account: ArtistAccount): String = when (account.status) {
    AccountStatus.Confirmed -> {
        val name = stringResource(R.string.artists_status_confirmed, account.alias.orEmpty())
        val source = sourceText(account.source)
        if (source != null) "$name · $source" else name
    }
    AccountStatus.Suggestion -> stringResource(R.string.artists_status_suggestion, account.suggestion.orEmpty())
    AccountStatus.Reuploader -> stringResource(R.string.artists_status_reuploader)
    AccountStatus.NotChecked -> stringResource(R.string.artists_status_not_checked)
    AccountStatus.Nick -> stringResource(R.string.artists_status_nick)
}

@Composable
private fun sourceText(source: String?): String? = when (source) {
    "library" -> stringResource(R.string.artists_source_library)
    "spotify" -> stringResource(R.string.artists_source_spotify)
    "deezer" -> stringResource(R.string.artists_source_deezer)
    "manual" -> stringResource(R.string.artists_source_manual)
    "title" -> stringResource(R.string.artists_source_title)
    else -> null
}
