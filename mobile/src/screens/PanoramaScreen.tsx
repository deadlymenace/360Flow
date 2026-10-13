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
} from 'react-native';
import { WebView } from 'react-native-webview';
import * as Sharing from 'expo-sharing';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';
import { RootStackParamList, ViewerMode } from '../navigation';
import { api, getApiOrigin } from '../services/api';
import { DISCLOSURE_TEXT, Project, STAGING_PRESETS } from '../models/types';

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
        if (!cancelled) setError(err instanceof Error ? err.message : 'Could not load the project');
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
  const viewerUrl = shareUrl ? `${shareUrl}?view=${mode}&embed=1` : null;
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
  const activity =
    project?.status === 'delayed'
      ? project.error || "Your 3D tour is delayed, we'll finish it shortly"
      : project?.staging_status === 'staging'
      ? 'Staging the room…'
      : originalDraftReady || stagedDraftReady
        ? 'Draft 3D is ready. The full walkthrough is still generating.'
        : buildingWorld
          ? 'Creating the 3D walkthrough…'
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
          ? { ...current, staging_status: 'staging', staging_error: null, staging_prompt: text }
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
    if (!shareUrl) return;
    await Share.share({ message: shareUrl, url: shareUrl });
  };

  const handleExportStaged = async () => {
    if (!project?.share_token) return;
    try {
      const url = `${getApiOrigin()}/v/${project.share_token}/staged.jpg?download=1`;
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
      <View style={styles.header}>
        <TouchableOpacity onPress={() => navigation.popToTop()}>
          <Text style={styles.backText}>← Home</Text>
        </TouchableOpacity>
        <Text style={styles.title} numberOfLines={1}>{projectName}</Text>
        <TouchableOpacity onPress={handleShare} disabled={!shareUrl}>
          <Text style={styles.backText}>Share</Text>
        </TouchableOpacity>
      </View>

      <View style={styles.viewer}>
        {viewerUrl ? (
          <WebView
            key={viewerUrl}
            source={{ uri: viewerUrl }}
            style={styles.web}
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
          <View style={styles.activity}>
            <ActivityIndicator color="#fff" />
            <Text style={styles.activityText}>{activity}</Text>
          </View>
        )}
      </View>

      <View style={styles.toolbar}>
        <View style={styles.modes}>
          {(['original', 'compare', 'staged'] as ViewerMode[]).map((item) => {
            const disabled = item !== 'original' && !project?.has_staged;
            return (
              <TouchableOpacity
                key={item}
                style={[styles.mode, mode === item && styles.modeOn, disabled && styles.disabled]}
                disabled={disabled}
                onPress={() => setMode(item)}
              >
                <Text style={styles.modeText}>
                  {item === 'original' ? 'Original' : item === 'compare' ? 'Compare' : 'Staged'}
                </Text>
              </TouchableOpacity>
            );
          })}
        </View>
        {project?.error ? <Text style={styles.errorDetail}>{project.error}</Text> : null}
        {project?.staging_status === 'failed' && project.staging_error ? (
          <Text style={styles.errorDetail}>{project.staging_error}</Text>
        ) : null}
        <TouchableOpacity
          style={[styles.primary, !project?.has_original && styles.disabled]}
          disabled={!project?.has_original}
          onPress={() => openWorld('original')}
        >
          <Text style={styles.primaryText}>
            {project?.world_preview_url && !project?.world_marble_url ? 'View draft 3D' : 'View 3D walkthrough'}
          </Text>
        </TouchableOpacity>
        <TouchableOpacity
          style={[styles.secondary, (!project?.has_original || buildingWorld || project?.staging_status === 'staging') && styles.disabled]}
          disabled={!project?.has_original || submitting || buildingWorld || project?.staging_status === 'staging'}
          onPress={() => setShowStage(true)}
        >
          <Text style={styles.secondaryText}>{project?.has_staged ? 'Stage again' : 'Optionally stage furniture'}</Text>
        </TouchableOpacity>
        {project?.has_staged && (
          <TouchableOpacity
            style={[styles.secondary, (submitting || buildingWorld) && styles.disabled]}
            disabled={submitting || buildingWorld}
            onPress={handleStagedWorld}
          >
            <Text style={styles.secondaryText}>
              {project.staged_world_preview_url && !project.staged_world_marble_url
                ? 'View draft staged 3D'
                : project.staged_world_marble_url || project.staged_world_viewer_url
                  ? 'View staged 3D'
                  : 'Create staged 3D'}
            </Text>
          </TouchableOpacity>
        )}
        {project?.status === 'failed' && (
          <TouchableOpacity onPress={handleRetry} disabled={submitting}>
            <Text style={styles.link}>Retry</Text>
          </TouchableOpacity>
        )}
        {project?.has_staged && (
          <TouchableOpacity onPress={handleExportStaged}>
            <Text style={styles.link}>Export staged image</Text>
          </TouchableOpacity>
        )}
        <TouchableOpacity
          onPress={() => {
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
                      await api.deleteProject(projectId);
                      navigation.popToTop();
                    } catch (err) {
                      Alert.alert('Could not delete', err instanceof Error ? err.message : 'Try again');
                    }
                  },
                },
              ],
            );
          }}
        >
          <Text style={styles.deleteLink}>Delete capture</Text>
        </TouchableOpacity>
      </View>

      <Modal visible={showStage} transparent animationType="slide">
        <View style={styles.modalOverlay}>
          <View style={styles.modal}>
            <Text style={styles.modalTitle}>Virtual staging</Text>
            <Text style={styles.modalBody}>
              Adds furniture and decor only. Walls, windows, floors, and fixtures stay as captured.
              The result is labeled {DISCLOSURE_TEXT} and can be compared with the original.
            </Text>
            <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.presets}>
              {STAGING_PRESETS.map((preset) => (
                <TouchableOpacity
                  key={preset.id}
                  style={[styles.preset, prompt === preset.prompt && styles.presetOn]}
                  onPress={() => setPrompt(preset.prompt)}
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
            />
            <View style={styles.modalButtons}>
              <TouchableOpacity style={styles.secondary} onPress={() => setShowStage(false)}>
                <Text style={styles.secondaryText}>Cancel</Text>
              </TouchableOpacity>
              <TouchableOpacity style={styles.primary} onPress={handleStage} disabled={submitting || !prompt.trim()}>
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
    paddingTop: 56,
    paddingHorizontal: 16,
    paddingBottom: 10,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    backgroundColor: '#1a1a2e',
  },
  backText: { color: '#6C63FF', fontSize: 16, fontWeight: '600', width: 70 },
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
    paddingBottom: 28,
    gap: 8,
    backgroundColor: '#1a1a2e',
  },
  modes: { flexDirection: 'row', gap: 8 },
  mode: {
    flex: 1,
    paddingVertical: 8,
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
    paddingVertical: 12,
    alignItems: 'center',
  },
  primaryText: { color: '#fff', fontWeight: '700' },
  secondary: {
    borderRadius: 12,
    paddingVertical: 12,
    alignItems: 'center',
    borderWidth: 1,
    borderColor: '#3d3866',
  },
  secondaryText: { color: '#fff', fontWeight: '600' },
  link: { color: '#ffe08a', textAlign: 'center', fontWeight: '600' },
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
  preset: { borderWidth: 1, borderColor: '#3d3866', borderRadius: 999, paddingHorizontal: 12, paddingVertical: 8 },
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
