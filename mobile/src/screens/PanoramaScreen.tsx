import React, { useEffect, useRef, useState } from 'react';
import {
  View,
  Text,
  StyleSheet,
  ActivityIndicator,
  TouchableOpacity,
  Modal,
  TextInput,
  Alert,
  Share,
  ScrollView,
  ActionSheetIOS,
  Platform,
  AlertButton,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import WebViewFrame from '../components/WebViewFrame';
import * as Sharing from 'expo-sharing';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';
import { RootStackParamList, ViewerMode } from '../navigation';
import { DeleteProjectError, markDeleted } from '../services/deletePolicy';
import { api } from '../services/api';
import { DISCLOSURE_TEXT, DRAFT_ETA_S, FULL_ETA_MIN, Project, STAGING_PRESETS } from '../models/types';

type Props = NativeStackScreenProps<RootStackParamList, 'Panorama'>;

export default function PanoramaScreen({ navigation, route }: Props) {
  const { projectId, projectName } = route.params;
  const [project, setProject] = useState<Project | null>(null);
  const [mode, setMode] = useState<ViewerMode>('original');
  const [error, setError] = useState<string | null>(null);
  const [showStage, setShowStage] = useState(false);
  const [prompt, setPrompt] = useState(STAGING_PRESETS[0].prompt);
  const [submitting, setSubmitting] = useState(false);
  const previousStatus = useRef<string | null>(null);
  const previousStaging = useRef<string | null>(null);
  const insets = useSafeAreaInsets();
  const buildStartRef = useRef<number | null>(null);
  const [now, setNow] = useState(Date.now());

  useEffect(() => {
    let cancelled = false;
    const refresh = async () => {
      try {
        const latest = await api.getProject(projectId);
        if (cancelled) return;
        setProject(latest);
        setError(null);
        if (previousStaging.current === 'staging' && latest.staging_status === 'staged') {
          setMode('compare');
        }
        if (previousStaging.current === 'staging' && latest.staging_status === 'failed') {
          Alert.alert('Staging failed', latest.staging_error || 'Try again');
        }
        if (latest.status === 'failed' && previousStatus.current && previousStatus.current !== 'failed') {
          Alert.alert('Something went wrong', latest.error || 'Try again from this screen.');
        }
        previousStatus.current = latest.status;
        previousStaging.current = latest.staging_status;
      } catch (err) {
        if (!cancelled) setError(__DEV__ && err instanceof Error ? err.message : "Couldn't reach 360Flow. Check your connection.");
      }
    };
    refresh();
    const timer = setInterval(refresh, 2500);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [projectId]);

  const shareUrl = project?.share_url;
  const viewerBase = project?.preview_url || shareUrl;
  const viewerUrl = viewerBase ? `${viewerBase}?view=${mode}&embed=1` : null;
  const showDisclosure = Boolean(project?.has_staged) && mode !== 'original';
  const buildingWorld =
    project?.active_job === 'world' ||
    project?.active_job === 'staged_world' ||
    project?.status === 'generating_3d' ||
    project?.status === 'delayed';
  const originalDraftReady = Boolean(project?.world_preview_url) && !project?.world_marble_url && buildingWorld;
  const stagedDraftReady =
    Boolean(project?.staged_world_preview_url) &&
    !project?.staged_world_marble_url &&
    project?.active_job === 'staged_world';
  // Elapsed time is measured from when this screen first saw the build. The
  // backend does not expose a start time, so it is a lower bound.
  useEffect(() => {
    if (!buildingWorld) {
      buildStartRef.current = null;
      return;
    }
    if (buildStartRef.current == null) buildStartRef.current = Date.now();
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [buildingWorld]);
  const elapsedS = buildStartRef.current ? Math.max(0, Math.round((now - buildStartRef.current) / 1000)) : 0;
  const elapsed = `${Math.floor(elapsedS / 60)}:${String(elapsedS % 60).padStart(2, '0')}`;
  const activity =
    project?.status === 'delayed'
      ? project.error || "Your 3D tour is delayed, we'll finish it shortly"
      : project?.staging_status === 'staging'
      ? 'Staging the room…'
      : originalDraftReady || stagedDraftReady
        ? `3D preview ready. Full tour still building (${FULL_ETA_MIN} min) · ${elapsed}`
        : buildingWorld
          ? `Building 3D: preview in ~${DRAFT_ETA_S} s, full tour in ${FULL_ETA_MIN} min · ${elapsed}`
          : null;
  const stageLocked = buildingWorld || project?.staging_status === 'staging';
  const stageLockReason = buildingWorld
    ? `Staging unlocks when the 3D tour finishes (~${FULL_ETA_MIN} min).`
    : project?.staging_status === 'staging'
      ? 'Staging is already running.'
      : !project?.has_original
        ? 'Staging unlocks once the panorama is ready.'
        : null;

  const handleStage = async () => {
    const text = prompt.trim();
    if (!text) return;
    setSubmitting(true);
    try {
      await api.startStaging(projectId, text);
      setShowStage(false);
      previousStaging.current = 'staging';
      setProject((current) =>
        current
          ? {
              ...current,
              staging_status: 'staging',
              staging_error: null,
              staging_prompt: text,
              staged_published: false,
            }
          : current,
      );
    } catch (err) {
      Alert.alert('Staging failed', err instanceof Error ? err.message : 'Try again');
    } finally {
      setSubmitting(false);
    }
  };

  const openWorld = (source: 'original' | 'staged') => {
    navigation.navigate('World', { projectId, projectName, source });
  };

  const handleStagedWorld = async () => {
    const stagedWorldFinished = project?.status === 'complete' && Boolean(project.staged_world_viewer_url);
    if (project?.active_job === 'staged_world' || stagedWorldFinished) {
      openWorld('staged');
      return;
    }
    setSubmitting(true);
    try {
      await api.startGenerate3D(projectId, 'staged');
      previousStatus.current = 'generating_3d';
      setProject((current) =>
        current ? { ...current, status: 'generating_3d', active_job: 'staged_world' } : current,
      );
      openWorld('staged');
    } catch (err) {
      Alert.alert('3D generation failed', err instanceof Error ? err.message : 'Try again');
    } finally {
      setSubmitting(false);
    }
  };

  const handleRetry = async () => {
    setSubmitting(true);
    try {
      await api.retry(projectId);
    } catch (err) {
      Alert.alert('Retry failed', err instanceof Error ? err.message : 'Try again');
    } finally {
      setSubmitting(false);
    }
  };

  const handleShare = async () => {
    if (!shareUrl) {
      Alert.alert('Share link not ready', 'The link appears once the panorama has finished processing.');
      return;
    }
    // Message only (no `url`) so iOS doesn't paste the link twice. The share
    // sheet's own "Copy" covers copy-link without a new native module.
    await Share.share({ message: `Take a 3D tour of ${projectName}: ${shareUrl}` });
  };

  const confirmDelete = () => {
    Alert.alert(
      'Delete this capture?',
      'This removes the photos, panorama, staged image, 3D walkthrough, and share link from 360Flow. This cannot be undone.',
      [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Delete',
          style: 'destructive',
          onPress: async () => {
            try {
              await api.deleteProject(projectId); // a 404 means already gone: fine
              markDeleted(projectId);
              navigation.popToTop();
            } catch (err) {
              Alert.alert('Could not delete', err instanceof DeleteProjectError ? err.message : 'Try again');
            }
          },
        },
      ],
    );
  };

  type MenuItem = { label: string; onPress: () => void; destructive?: boolean };
  const openMenu = () => {
    const items: MenuItem[] = [];
    if (project?.has_original) {
      items.push({
        label: project.has_staged ? 'Stage again' : 'Stage furniture (optional)',
        onPress: () => {
          if (stageLocked && stageLockReason) Alert.alert('Staging is locked', stageLockReason);
          else setShowStage(true);
        },
      });
    }
    if (project?.has_staged) {
      items.push({
        label:
          project.staged_world_preview_url && !project.staged_world_marble_url
            ? 'View staged 3D preview'
            : project.staged_world_marble_url || project.staged_world_viewer_url
              ? 'View staged 3D'
              : 'Create staged 3D',
        onPress: () => {
          if (buildingWorld && project.active_job !== 'staged_world') {
            Alert.alert('Please wait', `The 3D tour is still building (~${FULL_ETA_MIN} min).`);
          } else handleStagedWorld();
        },
      });
      items.push({
        label: project.staged_published ? 'Unpublish staged view' : 'Publish staged view',
        onPress: handlePublishToggle,
      });
      items.push({ label: 'Export staged image', onPress: handleExportStaged });
    }
    items.push({ label: 'Capture the next room', onPress: () => navigation.popToTop() });
    items.push({ label: 'Delete capture', onPress: confirmDelete, destructive: true });

    if (Platform.OS === 'ios') {
      const options = [...items.map((item) => item.label), 'Cancel'];
      ActionSheetIOS.showActionSheetWithOptions(
        {
          options,
          cancelButtonIndex: options.length - 1,
          destructiveButtonIndex: items.findIndex((item) => item.destructive),
          title: projectName,
        },
        (index) => {
          if (index < items.length) items[index].onPress();
        },
      );
    } else {
      const buttons: AlertButton[] = items.map((item) => ({
        text: item.label,
        style: item.destructive ? 'destructive' : 'default',
        onPress: item.onPress,
      }));
      buttons.push({ text: 'Cancel', style: 'cancel' });
      Alert.alert(projectName, undefined, buttons);
    }
  };

  const handlePublishToggle = async () => {
    if (!project) return;
    setSubmitting(true);
    try {
      const updated = project.staged_published
        ? await api.unpublishStaged(projectId)
        : await api.publishStaged(projectId);
      setProject(updated);
    } catch (err) {
      Alert.alert(
        project.staged_published ? 'Could not unpublish' : 'Could not publish',
        err instanceof Error ? err.message : 'Try again',
      );
    } finally {
      setSubmitting(false);
    }
  };

  const handleExportStaged = async () => {
    if (!project?.preview_url) return;
    try {
      const url = `${project.preview_url}/staged.jpg?download=1`;
      const uri = await api.downloadToCache(url, `virtually-staged-${projectId}.jpg`);
      if (await Sharing.isAvailableAsync()) {
        await Sharing.shareAsync(uri, { mimeType: 'image/jpeg', UTI: 'public.jpeg' });
      } else {
        await Share.share({ url: uri, message: DISCLOSURE_TEXT });
      }
    } catch (err) {
      Alert.alert('Export failed', err instanceof Error ? err.message : 'Try again');
    }
  };

  if (error && !project) {
    return (
      <View style={styles.centered}>
        <Text style={styles.errorText}>Could not open the panorama</Text>
        <Text style={styles.errorDetail}>{error}</Text>
        <TouchableOpacity style={styles.primary} onPress={() => navigation.popToTop()}>
          <Text style={styles.primaryText}>Back to Home</Text>
        </TouchableOpacity>
      </View>
    );
  }

  return (
    <View style={styles.container}>
      <View style={[styles.header, { paddingTop: insets.top + 8 }]}>
        <TouchableOpacity
          onPress={() => navigation.popToTop()}
          style={styles.hit}
          hitSlop={12}
          accessibilityRole="button"
          accessibilityLabel="Home"
        >
          <Text style={styles.backText}>← Home</Text>
        </TouchableOpacity>
        <Text style={styles.title} numberOfLines={1} accessibilityRole="header">{projectName}</Text>
        <TouchableOpacity
          onPress={openMenu}
          style={[styles.hit, { alignItems: 'flex-end' }]}
          hitSlop={12}
          accessibilityRole="button"
          accessibilityLabel="More actions"
        >
          <Text style={styles.moreText}>•••</Text>
        </TouchableOpacity>
      </View>

      <View style={styles.viewer}>
        {viewerUrl ? (
          <WebViewFrame
            uri={viewerUrl}
            style={styles.web}
            label="the panorama"
            allowsInlineMediaPlayback
            setSupportMultipleWindows={false}
          />
        ) : (
          <View style={styles.centered}>
            <ActivityIndicator size="large" color="#6C63FF" />
          </View>
        )}
        {showDisclosure && (
          <View style={styles.chip} pointerEvents="none">
            <Text style={styles.chipText}>{DISCLOSURE_TEXT}</Text>
          </View>
        )}
        {activity && (
          <View style={styles.activity} accessibilityLiveRegion="polite">
            <ActivityIndicator color="#fff" />
            <Text style={styles.activityText}>{activity}</Text>
          </View>
        )}
      </View>

      <View style={[styles.toolbar, { paddingBottom: insets.bottom + 12 }]}>
        {project?.has_staged ? (
          <View style={styles.modes} accessibilityRole="tablist">
            {(['original', 'compare', 'staged'] as ViewerMode[]).map((item) => {
              const label = item === 'original' ? 'Original' : item === 'compare' ? 'Compare' : 'Staged';
              return (
                <TouchableOpacity
                  key={item}
                  style={[styles.mode, mode === item && styles.modeOn]}
                  onPress={() => setMode(item)}
                  accessibilityRole="tab"
                  accessibilityLabel={label}
                  accessibilityState={{ selected: mode === item }}
                >
                  <Text style={styles.modeText}>{label}</Text>
                </TouchableOpacity>
              );
            })}
          </View>
        ) : null}
        {project?.status === 'failed' ? (
          <Text style={styles.errorDetail}>{project.error || "The last step didn't finish."}</Text>
        ) : null}
        {project?.staging_status === 'failed' ? (
          <Text style={styles.errorDetail}>{project.staging_error || "Staging didn't finish. Try again from •••."}</Text>
        ) : null}
        {project?.status === 'failed' ? (
          <TouchableOpacity
            style={[styles.primary, submitting && styles.disabled]}
            onPress={handleRetry}
            disabled={submitting}
            accessibilityRole="button"
          >
            <Text style={styles.primaryText}>Retry</Text>
          </TouchableOpacity>
        ) : (
          <TouchableOpacity
            style={[styles.primary, !project?.has_original && styles.disabled]}
            disabled={!project?.has_original}
            onPress={() => openWorld('original')}
            accessibilityRole="button"
            accessibilityState={{ disabled: !project?.has_original }}
          >
            <Text style={styles.primaryText}>
              {project?.world_marble_url
                ? 'View 3D tour'
                : project?.world_preview_url
                  ? '3D preview ready'
                  : 'View 3D tour'}
            </Text>
          </TouchableOpacity>
        )}
        <TouchableOpacity
          style={[styles.secondary, !shareUrl && styles.disabled]}
          onPress={handleShare}
          accessibilityRole="button"
          accessibilityState={{ disabled: !shareUrl }}
          accessibilityHint={shareUrl ? undefined : 'Available once the panorama is ready'}
        >
          <Text style={styles.secondaryText}>Share link</Text>
        </TouchableOpacity>
        {!shareUrl ? <Text style={styles.hint}>The share link appears once the panorama is ready.</Text> : null}
        {project?.has_original && stageLocked && stageLockReason ? (
          <Text style={styles.hint}>{stageLockReason}</Text>
        ) : null}
        {project?.has_staged && !project.staged_published ? (
          <Text style={styles.hint}>The staged view is private. Publish it from ••• to add it to your link.</Text>
        ) : null}
      </View>

      <Modal visible={showStage} transparent animationType="slide">
        <View style={styles.modalOverlay}>
          <View style={[styles.modal, { paddingBottom: insets.bottom + 20 }]}>
            <Text style={styles.modalTitle}>Virtual staging</Text>
            <Text style={styles.modalBody}>
              Adds furniture and decor only. Walls, windows, floors, and fixtures stay as captured.
              The result is labeled {DISCLOSURE_TEXT} and can be compared with the original.
              It stays private until you tap Publish staged view.
            </Text>
            <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.presets}>
              {STAGING_PRESETS.map((preset) => (
                <TouchableOpacity
                  key={preset.id}
                  style={[styles.preset, prompt === preset.prompt && styles.presetOn]}
                  onPress={() => setPrompt(preset.prompt)}
                  accessibilityRole="button"
                  accessibilityLabel={`${preset.label} style`}
                  accessibilityState={{ selected: prompt === preset.prompt }}
                >
                  <Text style={styles.presetText}>{preset.label}</Text>
                </TouchableOpacity>
              ))}
            </ScrollView>
            <TextInput
              style={styles.input}
              value={prompt}
              onChangeText={setPrompt}
              placeholder="Furniture and style"
              placeholderTextColor="#777"
              multiline
              accessibilityLabel="Furniture and style description"
            />
            <View style={styles.modalButtons}>
              <TouchableOpacity style={[styles.secondary, { flex: 1 }]} onPress={() => setShowStage(false)} accessibilityRole="button">
                <Text style={styles.secondaryText}>Cancel</Text>
              </TouchableOpacity>
              <TouchableOpacity
                style={[styles.primary, { flex: 1 }]}
                onPress={handleStage}
                disabled={submitting || !prompt.trim()}
                accessibilityRole="button"
                accessibilityLabel="Stage"
                accessibilityState={{ disabled: submitting || !prompt.trim(), busy: submitting }}
              >
                {submitting ? <ActivityIndicator color="#fff" /> : <Text style={styles.primaryText}>Stage</Text>}
              </TouchableOpacity>
            </View>
          </View>
        </View>
      </Modal>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#0e0e18' },
  viewer: { flex: 1, backgroundColor: '#000' },
  web: { flex: 1, backgroundColor: '#000' },
  header: {
    paddingHorizontal: 16,
    paddingBottom: 6,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    backgroundColor: '#1a1a2e',
  },
  backText: { color: '#8F88FF', fontSize: 16, fontWeight: '600' },
  moreText: { color: '#fff', fontSize: 20, fontWeight: '700', letterSpacing: 1 },
  hit: { minHeight: 44, minWidth: 70, justifyContent: 'center' },
  title: { color: '#fff', fontSize: 16, fontWeight: '700', flex: 1, textAlign: 'center' },
  chip: {
    position: 'absolute',
    top: 12,
    alignSelf: 'center',
    backgroundColor: '#1a1a2e',
    borderColor: 'rgba(255,224,138,0.75)',
    borderWidth: 1,
    borderRadius: 999,
    paddingHorizontal: 12,
    paddingVertical: 6,
  },
  chipText: { color: '#ffe08a', fontWeight: '700', fontSize: 13 },
  activity: {
    position: 'absolute',
    left: 16,
    right: 16,
    bottom: 16,
    backgroundColor: 'rgba(0,0,0,0.72)',
    borderRadius: 12,
    padding: 12,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
  },
  activityText: { color: '#fff', fontWeight: '600' },
  toolbar: {
    padding: 12,
    gap: 8,
    backgroundColor: '#1a1a2e',
  },
  modes: { flexDirection: 'row', gap: 8 },
  mode: {
    flex: 1,
    minHeight: 44,
    justifyContent: 'center',
    borderRadius: 999,
    borderWidth: 1,
    borderColor: '#3d3866',
    alignItems: 'center',
  },
  modeOn: { backgroundColor: '#6C63FF', borderColor: '#6C63FF' },
  modeText: { color: '#fff', fontWeight: '600', fontSize: 13 },
  primary: {
    backgroundColor: '#6C63FF',
    borderRadius: 12,
    minHeight: 48,
    paddingHorizontal: 16,
    justifyContent: 'center',
    alignItems: 'center',
  },
  primaryText: { color: '#fff', fontWeight: '700' },
  secondary: {
    borderRadius: 12,
    minHeight: 48,
    paddingHorizontal: 16,
    justifyContent: 'center',
    alignItems: 'center',
    borderWidth: 1,
    borderColor: '#3d3866',
  },
  secondaryText: { color: '#fff', fontWeight: '600' },
  link: { color: '#ffe08a', textAlign: 'center', fontWeight: '600' },
  hint: { color: '#c9c6e2', textAlign: 'center', fontSize: 13 },
  deleteLink: { color: '#F44336', textAlign: 'center', fontWeight: '700' },
  disabled: { opacity: 0.4 },
  centered: { flex: 1, backgroundColor: '#1a1a2e', justifyContent: 'center', alignItems: 'center', padding: 24 },
  errorText: { color: '#F44336', fontSize: 18, fontWeight: '700', marginBottom: 8 },
  errorDetail: { color: '#aaa', textAlign: 'center', marginBottom: 16 },
  modalOverlay: { flex: 1, backgroundColor: 'rgba(0,0,0,0.72)', justifyContent: 'flex-end' },
  modal: { backgroundColor: '#16213e', borderTopLeftRadius: 18, borderTopRightRadius: 18, padding: 20, gap: 12 },
  modalTitle: { color: '#fff', fontSize: 20, fontWeight: '700' },
  modalBody: { color: '#c9c6e2', fontSize: 14, lineHeight: 20 },
  presets: { gap: 8 },
  preset: { borderWidth: 1, borderColor: '#3d3866', borderRadius: 999, paddingHorizontal: 14, minHeight: 44, justifyContent: 'center' },
  presetOn: { backgroundColor: '#6C63FF', borderColor: '#6C63FF' },
  presetText: { color: '#fff', fontWeight: '600' },
  input: {
    minHeight: 72,
    borderRadius: 12,
    backgroundColor: '#1a1a2e',
    color: '#fff',
    padding: 12,
    textAlignVertical: 'top',
  },
  modalButtons: { flexDirection: 'row', gap: 10 },
});
