import React, { useState, useCallback } from 'react';
import {
  View,
  Text,
  FlatList,
  TouchableOpacity,
  StyleSheet,
  Alert,
  TextInput,
  Modal,
  RefreshControl,
  ActivityIndicator,
} from 'react-native';
import { useFocusEffect } from '@react-navigation/native';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';
import { RootStackParamList } from '../navigation';
import { api } from '../services/api';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Project, getStatusColor, liveStatusLabel, relativeTime } from '../models/types';
import { hasSeenCaptureCoach } from '../services/privacy';

const TRANSIENT = new Set(['uploading', 'stitching', 'staging', 'generating_3d', 'delayed']);
const LIVE_REFRESH_MS = 5000;

type Props = NativeStackScreenProps<RootStackParamList, 'Home'>;

export default function HomeScreen({ navigation }: Props) {
  const insets = useSafeAreaInsets();
  const [projects, setProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [showModal, setShowModal] = useState(false);
  const [roomName, setRoomName] = useState('');
  const [creating, setCreating] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);

  const loadProjects = useCallback(async () => {
    try {
      setLoadError(null);
      const data = await api.listProjects();
      setProjects(data);
    } catch (e: any) {
      setLoadError(e.message || 'Cannot connect to the backend');
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useFocusEffect(
    useCallback(() => {
      loadProjects();
    }, [loadProjects])
  );

  // Live row status while anything is still building and Home is visible.
  const anyBusy = projects.some(
    (p) => TRANSIENT.has(p.status) || p.active_job != null || p.staging_status === 'staging',
  );
  useFocusEffect(
    useCallback(() => {
      if (!anyBusy) return;
      const timer = setInterval(loadProjects, LIVE_REFRESH_MS);
      return () => clearInterval(timer);
    }, [anyBusy, loadProjects])
  );

  const openCapture = (project: Project) => {
    const next = { projectId: project.id, projectName: project.name };
    if (hasSeenCaptureCoach()) navigation.navigate('Capture', next);
    else navigation.navigate('CaptureCoach', { next });
  };

  const handleRefresh = () => {
    setRefreshing(true);
    loadProjects();
  };

  const handleCreate = async () => {
    const name = roomName.trim();
    if (!name) return;

    setCreating(true);
    try {
      const project = await api.createProject(name);
      setShowModal(false);
      setRoomName('');
      openCapture(project);
    } catch (e: any) {
      Alert.alert("Couldn't create the capture", __DEV__ ? e.message : 'Check your connection and try again.');
    } finally {
      setCreating(false);
    }
  };

  const openPanorama = (project: Project) => {
    navigation.navigate('Panorama', { projectId: project.id, projectName: project.name });
  };

  const handleProjectPress = (project: Project) => {
    if (project.status === 'created') {
      openCapture(project);
      return;
    }
    if (project.status === 'uploading' || project.status === 'stitching') {
      navigation.navigate('Processing', { projectId: project.id, projectName: project.name });
      return;
    }
    if (
      project.status === 'stitched' ||
      project.status === 'staging' ||
      project.status === 'staged' ||
      project.status === 'generating_3d' ||
      project.status === 'delayed' ||
      project.status === 'complete' ||
      project.has_original
    ) {
      openPanorama(project);
      return;
    }
    if (project.status === 'failed') {
      Alert.alert('Capture failed', project.error || 'The last step did not finish.', [
        { text: 'Dismiss', style: 'cancel' },
        {
          text: 'Retry',
          onPress: () =>
            navigation.navigate('Processing', {
              projectId: project.id,
              projectName: project.name,
              retry: true,
            }),
        },
      ]);
    }
  };

  const confirmDelete = (project: Project) => {
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
              await api.deleteProject(project.id);
              setProjects((current) => current.filter((item) => item.id !== project.id));
            } catch (e: any) {
              Alert.alert('Could not delete', e.message || 'Try again');
            }
          },
        },
      ],
    );
  };

  const renderProject = ({ item }: { item: Project }) => {
    const label = liveStatusLabel(item);
    const meta =
      item.status === 'created'
        ? relativeTime(item.created_at)
        : `${item.image_count} photos · ${relativeTime(item.created_at)}`;
    return (
    <View style={styles.projectRow}>
      <TouchableOpacity
        style={styles.projectInfo}
        onPress={() => handleProjectPress(item)}
        accessibilityRole="button"
        accessibilityLabel={`${item.name}. ${label}. ${meta}`}
      >
        <Text style={styles.projectName} numberOfLines={1}>{item.name}</Text>
        <Text style={styles.projectMeta}>{meta}</Text>
      </TouchableOpacity>
      <View style={styles.rowActions}>
        <View
          style={[styles.statusBadge, { backgroundColor: getStatusColor(item.status) + '20' }]}
          accessibilityElementsHidden
          importantForAccessibility="no-hide-descendants"
        >
          <Text style={[styles.statusText, { color: getStatusColor(item.status) }]}>{label}</Text>
        </View>
        <TouchableOpacity
          onPress={() => confirmDelete(item)}
          style={styles.deleteHit}
          hitSlop={8}
          accessibilityRole="button"
          accessibilityLabel={`Delete ${item.name}`}
        >
          <Text style={styles.deleteText}>Delete</Text>
        </TouchableOpacity>
      </View>
    </View>
    );
  };

  return (
    <View style={styles.container}>
      <View style={[styles.header, { paddingTop: insets.top + 12 }]}>
        <Text style={styles.title}>360Flow</Text>
        <TouchableOpacity style={styles.addButton} onPress={() => setShowModal(true)} accessibilityRole="button">
          <Text style={styles.addButtonText}>+ New Capture</Text>
        </TouchableOpacity>
      </View>

      {loading ? (
        <View style={styles.centered}>
          <ActivityIndicator size="large" color="#6C63FF" />
        </View>
      ) : loadError ? (
        <View style={styles.centered}>
          <Text style={styles.emptyIcon}>⚠️</Text>
          <Text style={styles.emptyTitle}>Cannot reach server</Text>
          <Text style={styles.emptySubtitle}>{loadError}</Text>
          <TouchableOpacity
            style={[styles.addButton, { marginTop: 20, paddingHorizontal: 24, paddingVertical: 12 }]}
            onPress={() => { setLoading(true); loadProjects(); }}
          >
            <Text style={styles.addButtonText}>Retry</Text>
          </TouchableOpacity>
        </View>
      ) : projects.length === 0 ? (
        <View style={styles.centered}>
          <Text style={styles.emptyIcon}>📸</Text>
          <Text style={styles.emptyTitle}>No captures yet</Text>
          <Text style={styles.emptySubtitle}>
            Tap "New Capture" to scan your first room
          </Text>
          <TouchableOpacity
            style={styles.howLink}
            onPress={() => navigation.navigate('CaptureCoach')}
            accessibilityRole="button"
          >
            <Text style={styles.howLinkText}>How it works</Text>
          </TouchableOpacity>
        </View>
      ) : (
        <FlatList
          data={projects}
          keyExtractor={(item) => item.id}
          renderItem={renderProject}
          contentContainerStyle={[styles.list, { paddingBottom: insets.bottom + 16 }]}
          refreshControl={
            <RefreshControl
              refreshing={refreshing}
              onRefresh={handleRefresh}
              tintColor="#6C63FF"
            />
          }
        />
      )}

      <Modal visible={showModal} transparent animationType="fade">
        <View style={styles.modalOverlay}>
          <View style={styles.modalContent}>
            <Text style={styles.modalTitle}>New Capture</Text>
            <Text style={styles.modalSubtitle}>Enter a name for this room</Text>
            <TextInput
              style={styles.input}
              placeholder="e.g., Living Room"
              placeholderTextColor="#666"
              value={roomName}
              onChangeText={setRoomName}
              autoFocus
              returnKeyType="done"
              onSubmitEditing={handleCreate}
            />
            <View style={styles.modalButtons}>
              <TouchableOpacity
                style={styles.cancelButton}
                onPress={() => { setShowModal(false); setRoomName(''); }}
                accessibilityRole="button"
              >
                <Text style={styles.cancelButtonText}>Cancel</Text>
              </TouchableOpacity>
              <TouchableOpacity
                style={[styles.createButton, !roomName.trim() && styles.disabledButton]}
                onPress={handleCreate}
                disabled={!roomName.trim() || creating}
                accessibilityRole="button"
                accessibilityState={{ disabled: !roomName.trim() || creating, busy: creating }}
              >
                {creating ? (
                  <ActivityIndicator size="small" color="#fff" />
                ) : (
                  <Text style={styles.createButtonText}>Start Capture</Text>
                )}
              </TouchableOpacity>
            </View>
          </View>
        </View>
      </Modal>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#1a1a2e',
  },
  header: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingHorizontal: 20,
    paddingBottom: 16,
  },
  howLink: { minHeight: 44, justifyContent: 'center', marginTop: 12, paddingHorizontal: 12 },
  howLinkText: { color: '#8F88FF', fontSize: 15, fontWeight: '600' },
  deleteHit: { minHeight: 32, minWidth: 44, justifyContent: 'center', alignItems: 'flex-end' },
  title: {
    fontSize: 28,
    fontWeight: '700',
    color: '#fff',
  },
  addButton: {
    backgroundColor: '#6C63FF',
    paddingHorizontal: 16,
    minHeight: 44,
    justifyContent: 'center',
    borderRadius: 12,
  },
  addButtonText: {
    color: '#fff',
    fontWeight: '600',
    fontSize: 14,
  },
  centered: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
    paddingHorizontal: 40,
  },
  emptyIcon: {
    fontSize: 48,
    marginBottom: 16,
  },
  emptyTitle: {
    fontSize: 20,
    fontWeight: '600',
    color: '#fff',
    marginBottom: 8,
  },
  emptySubtitle: {
    fontSize: 14,
    color: '#888',
    textAlign: 'center',
  },
  list: {
    padding: 16,
  },
  projectRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    backgroundColor: '#16213e',
    padding: 16,
    borderRadius: 12,
    marginBottom: 10,
  },
  projectInfo: {
    flex: 1,
    marginRight: 12,
  },
  projectName: {
    fontSize: 16,
    fontWeight: '600',
    color: '#fff',
    marginBottom: 4,
  },
  projectMeta: {
    fontSize: 13,
    color: '#b8b5d1',
  },
  statusBadge: {
    paddingHorizontal: 10,
    paddingVertical: 4,
    borderRadius: 8,
  },
  statusText: {
    fontSize: 12,
    fontWeight: '600',
  },
  rowActions: {
    alignItems: 'flex-end',
    gap: 8,
  },
  deleteText: {
    color: '#F44336',
    fontSize: 13,
    fontWeight: '700',
  },
  modalOverlay: {
    flex: 1,
    backgroundColor: 'rgba(0,0,0,0.7)',
    justifyContent: 'center',
    alignItems: 'center',
    padding: 24,
  },
  modalContent: {
    backgroundColor: '#16213e',
    borderRadius: 16,
    padding: 24,
    width: '100%',
  },
  modalTitle: {
    fontSize: 20,
    fontWeight: '700',
    color: '#fff',
    marginBottom: 4,
  },
  modalSubtitle: {
    fontSize: 14,
    color: '#888',
    marginBottom: 20,
  },
  input: {
    backgroundColor: '#1a1a2e',
    borderRadius: 10,
    padding: 14,
    fontSize: 16,
    color: '#fff',
    marginBottom: 20,
  },
  modalButtons: {
    flexDirection: 'row',
    gap: 12,
  },
  cancelButton: {
    flex: 1,
    padding: 14,
    borderRadius: 10,
    borderWidth: 1,
    borderColor: '#333',
    alignItems: 'center',
  },
  cancelButtonText: {
    color: '#888',
    fontWeight: '600',
  },
  createButton: {
    flex: 1,
    padding: 14,
    borderRadius: 10,
    backgroundColor: '#6C63FF',
    alignItems: 'center',
  },
  createButtonText: {
    color: '#fff',
    fontWeight: '600',
  },
  disabledButton: {
    opacity: 0.5,
  },
});
