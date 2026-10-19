import React, { useEffect, useState } from 'react';
import { View, Text, StyleSheet, ActivityIndicator, TouchableOpacity } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import WebViewFrame from '../components/WebViewFrame';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';
import { RootStackParamList } from '../navigation';
import { api } from '../services/api';
import { DISCLOSURE_TEXT, FULL_ETA_MIN, Project, WORLD_GENERATION_WAIT_MS } from '../models/types';

type Props = NativeStackScreenProps<RootStackParamList, 'World'>;

export default function WorldScreen({ navigation, route }: Props) {
  const { projectId, projectName, source = 'original' } = route.params;
  const staged = source === 'staged';
  const insets = useSafeAreaInsets();
  const [project, setProject] = useState<Project | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [waitedLong, setWaitedLong] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const latest = await api.getProject(projectId);
        if (cancelled) return;
        setProject(latest);
        if (latest.status === 'failed' && latest.error) {
          setError(latest.error);
        } else {
          setError(null);
        }
      } catch (err) {
        if (!cancelled) setError(__DEV__ && err instanceof Error ? err.message : "Couldn't reach 360Flow. Retrying…");
      }
    };
    load();
    const timer = setInterval(load, 3000);
    const budget = setTimeout(() => setWaitedLong(true), WORLD_GENERATION_WAIT_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
      clearTimeout(budget);
    };
  }, [projectId]);

  const fullUrl = staged ? project?.staged_world_marble_url : project?.world_marble_url;
  const previewUrl = staged ? project?.staged_world_preview_url : project?.world_preview_url;
  const viewerUrl = fullUrl || previewUrl || (staged ? project?.staged_world_viewer_url : project?.world_viewer_url) || null;
  const showingDraft = Boolean(previewUrl && !fullUrl);
  const delayed = project?.status === 'delayed';
  const stillGenerating =
    project?.status === 'generating_3d' ||
    delayed ||
    ((project?.active_job === 'world' || project?.active_job === 'staged_world') &&
      project?.status !== 'failed' &&
      project?.status !== 'complete');

  return (
    <View style={styles.container}>
      <View style={[styles.header, { paddingTop: insets.top + 8 }]}>
        <TouchableOpacity
          onPress={() => navigation.goBack()}
          style={styles.hit}
          hitSlop={12}
          accessibilityRole="button"
          accessibilityLabel="Back to panorama"
        >
          <Text style={styles.back}>← Panorama</Text>
        </TouchableOpacity>
        <Text style={styles.title} numberOfLines={1}>{projectName}</Text>
        <View style={{ width: 90 }} />
      </View>
      <View style={styles.viewer}>
        {!project && !error && (
          <View style={styles.centered}>
            <ActivityIndicator size="large" color="#6C63FF" />
          </View>
        )}
        {project && !viewerUrl && !delayed && !error && (
          <View style={styles.centered}>
            <ActivityIndicator size="large" color="#6C63FF" />
            <Text style={styles.waiting}>
              Building your 3D tour. A preview usually appears in under a minute; the full walkthrough takes{' '}
              {FULL_ETA_MIN} min.
            </Text>
          </View>
        )}
        {delayed && !viewerUrl && (
          <View style={styles.centered}>
            <ActivityIndicator size="large" color="#6C63FF" />
            <Text style={styles.waiting}>
              {project?.error || "Your 3D tour is delayed, we'll finish it shortly"}
            </Text>
          </View>
        )}
        {error && !viewerUrl && !delayed && (
          <View style={styles.centered}>
            <Text style={styles.error}>{error}</Text>
          </View>
        )}
        {viewerUrl && <WebViewFrame uri={viewerUrl} style={styles.web} label="the 3D tour" />}
        {delayed && viewerUrl && (
          <View style={styles.draft} pointerEvents="none">
            <Text style={styles.draftText}>
              {project?.error || "Your 3D tour is delayed, we'll finish it shortly"}
            </Text>
          </View>
        )}
        {showingDraft && stillGenerating && !delayed && (
          <View style={styles.draft} pointerEvents="none">
            <Text style={styles.draftText}>
              3D preview. The full walkthrough is still building ({FULL_ETA_MIN} min). You can leave; we'll keep working.
            </Text>
          </View>
        )}
        {waitedLong && stillGenerating && !fullUrl && !showingDraft && (
          <View style={styles.draft} pointerEvents="none">
            <Text style={styles.draftText}>Taking longer than usual. Still building the full walkthrough.</Text>
          </View>
        )}
        {staged && (
          <View style={styles.chip} pointerEvents="none">
            <Text style={styles.chipText}>{DISCLOSURE_TEXT}</Text>
          </View>
        )}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#0e0e18' },
  header: {
    paddingHorizontal: 16,
    paddingBottom: 6,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    backgroundColor: '#1a1a2e',
  },
  back: { color: '#8F88FF', fontWeight: '600', fontSize: 16 },
  hit: { minHeight: 44, width: 90, justifyContent: 'center' },
  title: { color: '#fff', fontWeight: '700', flex: 1, textAlign: 'center' },
  viewer: { flex: 1 },
  web: { flex: 1, backgroundColor: '#000' },
  centered: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: 24 },
  error: { color: '#F44336', textAlign: 'center' },
  waiting: { color: '#fff', textAlign: 'center', marginTop: 16, lineHeight: 22 },
  draft: {
    position: 'absolute',
    left: 16,
    right: 16,
    bottom: 16,
    backgroundColor: 'rgba(14,14,24,0.88)',
    borderRadius: 12,
    padding: 12,
  },
  draftText: { color: '#fff', fontWeight: '600', textAlign: 'center' },
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
  chipText: { color: '#ffe08a', fontWeight: '700' },
});
