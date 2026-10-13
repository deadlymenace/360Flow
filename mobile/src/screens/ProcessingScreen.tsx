import React, { useState, useEffect, useRef } from 'react';
import {
  View,
  Text,
  TouchableOpacity,
  StyleSheet,
  ActivityIndicator,
} from 'react-native';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';
import { RootStackParamList } from '../navigation';
import { api } from '../services/api';
import { HapticFeedback } from '../utils/haptics';

type Props = NativeStackScreenProps<RootStackParamList, 'Processing'>;

type Phase = 'uploading' | 'stitching' | 'done' | 'error';

export default function ProcessingScreen({ navigation, route }: Props) {
  const { projectId, projectName, imageUris, yaws, hfov, lens, retry } = route.params;
  const [phase, setPhase] = useState<Phase>('uploading');
  const [uploadProgress, setUploadProgress] = useState(0);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const pollingRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    if (imageUris && imageUris.length > 0) {
      startUpload();
    } else if (retry) {
      startRetry();
    } else {
      setPhase('stitching');
      startPolling();
    }

    return () => {
      if (pollingRef.current) clearInterval(pollingRef.current);
    };
  }, []);

  const startUpload = async () => {
    try {
      setPhase('uploading');
      await api.uploadImages(projectId, imageUris!, {
        yaws,
        hfov,
        lens,
        onProgress: (progress) => setUploadProgress(progress),
      });
      setPhase('stitching');
      startPolling();
    } catch (e: any) {
      setPhase('error');
      setErrorMsg(e.message);
      HapticFeedback.error();
    }
  };

  const startRetry = async () => {
    try {
      setPhase('stitching');
      await api.retry(projectId);
      startPolling();
    } catch (e: any) {
      setPhase('error');
      setErrorMsg(e.message);
      HapticFeedback.error();
    }
  };

  const startPolling = () => {
    let settled = false;
    const tick = async () => {
      if (settled) return;
      try {
        const status = await api.getStatus(projectId);
        if (status.has_original) {
          settled = true;
          if (pollingRef.current) clearInterval(pollingRef.current);
          setPhase('done');
          HapticFeedback.success();
        } else if (status.status === 'failed') {
          settled = true;
          if (pollingRef.current) clearInterval(pollingRef.current);
          setPhase('error');
          setErrorMsg(status.error || 'Stitching failed');
          HapticFeedback.error();
        }
      } catch (e: any) {
        console.warn('Polling error:', e.message);
      }
    };
    tick();
    pollingRef.current = setInterval(tick, 2000);
  };

  const handleRetry = () => {
    setErrorMsg(null);
    if (imageUris && imageUris.length > 0) {
      startUpload();
    } else {
      startRetry();
    }
  };

  const handleViewPanorama = () => {
    navigation.replace('Panorama', { projectId, projectName });
  };

  return (
    <View style={styles.container}>
      {phase === 'uploading' && (
        <View style={styles.centered}>
          <ActivityIndicator size="large" color="#6C63FF" style={styles.spinner} />
          <Text style={styles.phaseTitle}>Uploading photos...</Text>
          <View style={styles.progressBarOuter}>
            <View style={[styles.progressBarInner, { width: `${uploadProgress * 100}%` }]} />
          </View>
          <Text style={styles.progressText}>{Math.round(uploadProgress * 100)}%</Text>
        </View>
      )}

      {phase === 'stitching' && (
        <View style={styles.centered}>
          <ActivityIndicator size="large" color="#6C63FF" style={styles.spinner} />
          <Text style={styles.phaseTitle}>Stitching your panorama...</Text>
          <Text style={styles.subtitle}>The panorama opens as soon as it is ready. The 3D walkthrough continues in the background.</Text>
        </View>
      )}

      {phase === 'done' && (
        <View style={styles.centered}>
          <View style={styles.successIcon}>
            <Text style={styles.successCheck}>✓</Text>
          </View>
          <Text style={styles.phaseTitle}>Panorama ready!</Text>
          <Text style={styles.subtitle}>You can look around now. The 3D walkthrough keeps generating.</Text>
          <TouchableOpacity style={styles.viewButton} onPress={handleViewPanorama}>
            <Text style={styles.viewButtonText}>View Panorama →</Text>
          </TouchableOpacity>
        </View>
      )}

      {phase === 'error' && (
        <View style={styles.centered}>
          <View style={styles.errorIcon}>
            <Text style={styles.errorX}>!</Text>
          </View>
          <Text style={styles.phaseTitle}>Something went wrong</Text>
          <Text style={styles.errorText}>{errorMsg}</Text>
          <TouchableOpacity style={styles.retryButton} onPress={handleRetry}>
            <Text style={styles.retryButtonText}>Retry</Text>
          </TouchableOpacity>
          <TouchableOpacity onPress={() => navigation.popToTop()}>
            <Text style={styles.homeLink}>Back to Home</Text>
          </TouchableOpacity>
        </View>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#1a1a2e',
  },
  centered: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
    padding: 40,
  },
  spinner: {
    marginBottom: 24,
  },
  phaseTitle: {
    fontSize: 20,
    fontWeight: '700',
    color: '#fff',
    marginBottom: 8,
  },
  subtitle: {
    fontSize: 14,
    color: '#888',
  },
  progressBarOuter: {
    width: '100%',
    height: 6,
    backgroundColor: '#333',
    borderRadius: 3,
    marginTop: 20,
    overflow: 'hidden',
  },
  progressBarInner: {
    height: '100%',
    backgroundColor: '#6C63FF',
    borderRadius: 3,
  },
  progressText: {
    color: '#888',
    fontSize: 14,
    marginTop: 8,
  },
  successIcon: {
    width: 64,
    height: 64,
    borderRadius: 32,
    backgroundColor: '#4CAF50',
    justifyContent: 'center',
    alignItems: 'center',
    marginBottom: 20,
  },
  successCheck: {
    color: '#fff',
    fontSize: 28,
    fontWeight: '700',
  },
  viewButton: {
    backgroundColor: '#6C63FF',
    paddingHorizontal: 32,
    paddingVertical: 16,
    borderRadius: 14,
    marginTop: 24,
  },
  viewButtonText: {
    color: '#fff',
    fontSize: 16,
    fontWeight: '700',
  },
  errorIcon: {
    width: 64,
    height: 64,
    borderRadius: 32,
    backgroundColor: '#F44336',
    justifyContent: 'center',
    alignItems: 'center',
    marginBottom: 20,
  },
  errorX: {
    color: '#fff',
    fontSize: 28,
    fontWeight: '700',
  },
  errorText: {
    color: '#F44336',
    fontSize: 14,
    textAlign: 'center',
    marginTop: 8,
    marginBottom: 24,
  },
  retryButton: {
    backgroundColor: '#6C63FF',
    paddingHorizontal: 32,
    paddingVertical: 14,
    borderRadius: 14,
    marginBottom: 16,
  },
  retryButtonText: {
    color: '#fff',
    fontSize: 16,
    fontWeight: '600',
  },
  homeLink: {
    color: '#6C63FF',
    fontSize: 14,
  },
});
