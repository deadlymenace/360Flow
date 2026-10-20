import React, { useState, useEffect, useRef, useCallback } from 'react';
import { View, Text, TouchableOpacity, StyleSheet, ActivityIndicator, Animated, Easing, AppState } from 'react-native';
import { useKeepAwake } from 'expo-keep-awake';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';
import { RootStackParamList } from '../navigation';
import { api } from '../services/api';
import { HapticFeedback } from '../utils/haptics';
import { DRAFT_ETA_S, FULL_ETA_MIN, STITCH_SLOW_MS } from '../models/types';
import { friendlyUploadMessage, newUploadSession, runUploadSession } from '../services/uploadPolicy';

type Props = NativeStackScreenProps<RootStackParamList, 'Processing'>;

type Phase = 'preparing' | 'sending' | 'checking' | 'stitching' | 'slow' | 'done' | 'error';

const POLL_MS = 2000;
const RECONNECT_AFTER_FAILURES = 3;

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export default function ProcessingScreen({ navigation, route }: Props) {
  const { projectId, projectName, imageUris, yaws, hfov, lens, retry } = route.params;
  const insets = useSafeAreaInsets();
  const [phase, setPhase] = useState<Phase>('preparing');
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [reconnecting, setReconnecting] = useState(false);
  const pollingRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const slowTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const mountedRef = useRef(true);
  const busyRef = useRef(false);
  const phaseRef = useRef<Phase>('preparing');
  phaseRef.current = phase;
  // Uploads and stitching can take a while; don't let the screen sleep.
  useKeepAwake();

  const stopPolling = () => {
    if (pollingRef.current) clearInterval(pollingRef.current);
    if (slowTimerRef.current) clearTimeout(slowTimerRef.current);
    pollingRef.current = null;
    slowTimerRef.current = null;
  };

  const fail = (message: string) => {
    if (!mountedRef.current) return;
    stopPolling();
    setPhase('error');
    setErrorMsg(message);
    HapticFeedback.error();
  };

  const startPolling = useCallback(() => {
    stopPolling();
    if (!mountedRef.current) return;
    setPhase('stitching');
    setReconnecting(false);
    let settled = false;
    let failures = 0;
    const tick = async () => {
      if (settled || !mountedRef.current) return;
      try {
        const status = await api.getStatus(projectId);
        failures = 0;
        if (mountedRef.current) setReconnecting(false);
        if (status.has_original) {
          settled = true;
          stopPolling();
          if (mountedRef.current) setPhase('done');
          HapticFeedback.success();
        } else if (status.status === 'failed') {
          settled = true;
          fail(status.error || "Stitching didn't work. Retry, or capture the room again.");
        }
      } catch (e: any) {
        failures += 1;
        if (__DEV__) console.warn('Polling error:', e?.message);
        if (failures >= RECONNECT_AFTER_FAILURES && mountedRef.current) setReconnecting(true);
      }
    };
    tick();
    pollingRef.current = setInterval(tick, POLL_MS);
    // Stitching should be quick. A spinner that never ends is worse than a choice.
    slowTimerRef.current = setTimeout(() => {
      if (!settled && mountedRef.current) setPhase('slow');
    }, STITCH_SLOW_MS);
  }, [projectId]);

  // One session per photo set: remembers whether any attempt reached the
  // send step, so a manual Retry checks status before uploading again.
  const sessionRef = useRef(newUploadSession());

  /** Billing-safe upload; the policy lives in services/uploadPolicy.ts. */
  const startUpload = useCallback(async () => {
    if (busyRef.current || !imageUris?.length) return;
    busyRef.current = true;
    setErrorMsg(null);
    try {
      const outcome = await runUploadSession(sessionRef.current, {
        upload: () =>
          api.uploadImages(projectId, imageUris, {
            yaws,
            hfov,
            lens,
            onStage: (stage) => mountedRef.current && setPhase(stage),
          }),
        getStatus: async () => (await api.getStatus(projectId)).status,
        sleep,
        onPhase: (p) => mountedRef.current && setPhase(p),
      });
      if (!mountedRef.current) return;
      if (outcome.result === 'poll') startPolling();
      else fail(friendlyUploadMessage(outcome.error));
    } finally {
      busyRef.current = false;
    }
  }, [projectId, imageUris, yaws, hfov, lens, startPolling]);

  const startRetry = useCallback(async () => {
    try {
      setPhase('stitching');
      await api.retry(projectId);
      startPolling();
    } catch (e: any) {
      fail(__DEV__ ? e?.message : "Couldn't restart processing. Check your connection and try again.");
    }
  }, [projectId, startPolling]);

  // Back from the background (timers may have been paused): check the project status right away.
  useEffect(() => {
    const sub = AppState.addEventListener('change', (next) => {
      if (next !== 'active' || !mountedRef.current || busyRef.current) return;
      const p = phaseRef.current;
      if (p === 'stitching' || p === 'slow') startPolling();
    });
    return () => sub.remove();
  }, [startPolling]);

  useEffect(() => {
    mountedRef.current = true;
    if (imageUris && imageUris.length > 0) {
      startUpload();
    } else if (retry) {
      startRetry();
    } else {
      startPolling();
    }
    return () => {
      mountedRef.current = false;
      stopPolling();
    };
  }, []);

  const handleRetry = () => {
    setErrorMsg(null);
    // Manual Retry reuses the session: if any attempt reached the send step,
    // runUploadSession checks status first, so it cannot start a second paid run.
    if (imageUris && imageUris.length > 0) {
      startUpload();
    } else {
      startRetry();
    }
  };

  const handleViewPanorama = () => navigation.replace('Panorama', { projectId, projectName });
  const goHome = () => navigation.popToTop();

  const uploading = phase === 'preparing' || phase === 'sending' || phase === 'checking';
  const uploadTitle =
    phase === 'preparing'
      ? `Preparing ${imageUris?.length ?? 16} photos…`
      : phase === 'sending'
        ? 'Sending to 360Flow…'
        : 'Checking upload…';

  return (
    <View style={[styles.container, { paddingTop: insets.top, paddingBottom: insets.bottom }]}>
      {uploading && (
        <View style={styles.centered} accessibilityLiveRegion="polite">
          <ActivityIndicator size="large" color="#8F88FF" style={styles.spinner} />
          <Text style={styles.phaseTitle} accessibilityRole="header">{uploadTitle}</Text>
          <IndeterminateBar />
          <Text style={styles.subtitle}>
            {phase === 'sending' ? 'This can take up to 2 minutes on mobile data.' : ' '}
          </Text>
          <Text style={styles.keepOpen}>Keep 360Flow open until the upload finishes.</Text>
        </View>
      )}

      {(phase === 'stitching' || phase === 'slow') && (
        <View style={styles.centered} accessibilityLiveRegion="polite">
          <ActivityIndicator size="large" color="#8F88FF" style={styles.spinner} />
          <Text style={styles.phaseTitle} accessibilityRole="header">
            {phase === 'slow' ? 'Taking longer than usual' : 'Stitching your panorama…'}
          </Text>
          <Text style={styles.subtitle}>
            {reconnecting
              ? 'Reconnecting…'
              : phase === 'slow'
                ? 'You can retry, or come back later from Home.'
                : 'The panorama opens as soon as it is ready.'}
          </Text>
          {phase === 'slow' && (
            <>
              <TouchableOpacity style={styles.retryButton} onPress={startRetry} accessibilityRole="button">
                <Text style={styles.retryButtonText}>Retry</Text>
              </TouchableOpacity>
              <TouchableOpacity style={styles.linkButton} onPress={goHome} accessibilityRole="button">
                <Text style={styles.homeLink}>Back to Home</Text>
              </TouchableOpacity>
            </>
          )}
        </View>
      )}

      {phase === 'done' && (
        <View style={styles.centered}>
          <View style={styles.successIcon} accessible={false}>
            <Text style={styles.successCheck}>✓</Text>
          </View>
          <Text style={styles.phaseTitle} accessibilityRole="header">Panorama ready</Text>
          <Text style={styles.subtitle}>
            3D preview in ~{DRAFT_ETA_S} s, full walkthrough in {FULL_ETA_MIN} min. You can leave this screen; we'll
            keep working.
          </Text>
          <TouchableOpacity style={styles.viewButton} onPress={handleViewPanorama} accessibilityRole="button">
            <Text style={styles.viewButtonText}>View panorama</Text>
          </TouchableOpacity>
          <TouchableOpacity style={styles.linkButton} onPress={goHome} accessibilityRole="button"
            accessibilityHint="Goes Home so you can start a new capture while this one builds">
            <Text style={styles.homeLink}>Capture the next room while this builds</Text>
          </TouchableOpacity>
        </View>
      )}

      {phase === 'error' && (
        <View style={styles.centered}>
          <View style={styles.errorIcon} accessible={false}>
            <Text style={styles.errorX}>!</Text>
          </View>
          <Text style={styles.phaseTitle} accessibilityRole="header">Something went wrong</Text>
          <Text style={styles.errorText}>{errorMsg}</Text>
          <TouchableOpacity style={styles.retryButton} onPress={handleRetry} accessibilityRole="button">
            <Text style={styles.retryButtonText}>Retry</Text>
          </TouchableOpacity>
          <TouchableOpacity style={styles.linkButton} onPress={goHome} accessibilityRole="button">
            <Text style={styles.homeLink}>Back to Home</Text>
          </TouchableOpacity>
        </View>
      )}
    </View>
  );
}

function IndeterminateBar() {
  const anim = useRef(new Animated.Value(0)).current;
  useEffect(() => {
    const loop = Animated.loop(
      Animated.timing(anim, { toValue: 1, duration: 1200, easing: Easing.inOut(Easing.ease), useNativeDriver: true }),
    );
    loop.start();
    return () => loop.stop();
  }, [anim]);
  const translateX = anim.interpolate({ inputRange: [0, 1], outputRange: [-120, 280] });
  return (
    <View style={styles.progressBarOuter} accessibilityRole="progressbar" accessibilityLabel="Uploading">
      <Animated.View style={[styles.progressBarInner, { transform: [{ translateX }] }]} />
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#1a1a2e' },
  centered: { flex: 1, justifyContent: 'center', alignItems: 'center', padding: 32 },
  spinner: { marginBottom: 24 },
  phaseTitle: { fontSize: 20, fontWeight: '700', color: '#fff', marginBottom: 8, textAlign: 'center' },
  subtitle: { fontSize: 15, color: '#b8b5d1', textAlign: 'center', marginTop: 8, lineHeight: 21 },
  keepOpen: { fontSize: 13, color: '#ffe08a', textAlign: 'center', marginTop: 16 },
  progressBarOuter: {
    width: '100%',
    height: 6,
    backgroundColor: '#333',
    borderRadius: 3,
    marginTop: 20,
    overflow: 'hidden',
  },
  progressBarInner: { width: 120, height: '100%', backgroundColor: '#6C63FF', borderRadius: 3 },
  successIcon: {
    width: 64,
    height: 64,
    borderRadius: 32,
    backgroundColor: '#4CAF50',
    justifyContent: 'center',
    alignItems: 'center',
    marginBottom: 20,
  },
  successCheck: { color: '#fff', fontSize: 28, fontWeight: '700' },
  viewButton: {
    backgroundColor: '#6C63FF',
    paddingHorizontal: 32,
    minHeight: 52,
    justifyContent: 'center',
    borderRadius: 14,
    marginTop: 24,
  },
  viewButtonText: { color: '#fff', fontSize: 16, fontWeight: '700' },
  errorIcon: {
    width: 64,
    height: 64,
    borderRadius: 32,
    backgroundColor: '#F44336',
    justifyContent: 'center',
    alignItems: 'center',
    marginBottom: 20,
  },
  errorX: { color: '#fff', fontSize: 28, fontWeight: '700' },
  errorText: { color: '#ff8a80', fontSize: 15, textAlign: 'center', marginTop: 8, marginBottom: 24, lineHeight: 21 },
  retryButton: {
    backgroundColor: '#6C63FF',
    paddingHorizontal: 32,
    minHeight: 48,
    justifyContent: 'center',
    borderRadius: 14,
    marginTop: 16,
  },
  retryButtonText: { color: '#fff', fontSize: 16, fontWeight: '600' },
  linkButton: { minHeight: 44, justifyContent: 'center', paddingHorizontal: 12, marginTop: 8 },
  homeLink: { color: '#8F88FF', fontSize: 15, fontWeight: '600', textAlign: 'center' },
});
