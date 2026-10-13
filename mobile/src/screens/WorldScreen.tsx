import React, { useEffect, useState } from 'react';
import { View, Text, StyleSheet, ActivityIndicator, TouchableOpacity } from 'react-native';
import { WebView } from 'react-native-webview';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';
import { RootStackParamList } from '../navigation';
import { api } from '../services/api';
import { DISCLOSURE_TEXT, Project } from '../models/types';

type Props = NativeStackScreenProps<RootStackParamList, 'World'>;

export default function WorldScreen({ navigation, route }: Props) {
  const { projectId, projectName } = route.params;
  const [project, setProject] = useState<Project | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    api.getProject(projectId)
      .then((latest) => {
        if (!cancelled) setProject(latest);
      })
      .catch((err: Error) => {
        if (!cancelled) setError(err.message);
      });
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  const viewerUrl = project?.world_marble_url || project?.world_viewer_url || null;

  return (
    <View style={styles.container}>
      <View style={styles.header}>
        <TouchableOpacity onPress={() => navigation.goBack()}>
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
        {error && (
          <View style={styles.centered}>
            <Text style={styles.error}>{error}</Text>
          </View>
        )}
        {viewerUrl && <WebView source={{ uri: viewerUrl }} style={styles.web} />}
        {project?.has_staged && (
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
    paddingTop: 56,
    paddingHorizontal: 16,
    paddingBottom: 10,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    backgroundColor: '#1a1a2e',
  },
  back: { color: '#6C63FF', fontWeight: '600', width: 90 },
  title: { color: '#fff', fontWeight: '700', flex: 1, textAlign: 'center' },
  viewer: { flex: 1 },
  web: { flex: 1, backgroundColor: '#000' },
  centered: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: 24 },
  error: { color: '#F44336', textAlign: 'center' },
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
