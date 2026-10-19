import React, { useState, useEffect, useRef, useCallback } from 'react';
import {
  View,
  Text,
  TouchableOpacity,
  StyleSheet,
  AccessibilityInfo,
  Alert,
  Linking,
  useWindowDimensions,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { CameraView, useCameraPermissions } from 'expo-camera';
import { DeviceMotion, DeviceMotionMeasurement } from 'expo-sensors';
import Svg, { Circle, Line } from 'react-native-svg';
import { useIsFocused } from '@react-navigation/native';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';
import { RootStackParamList } from '../navigation';
import {
  CaptureSegment,
  SEGMENT_ANGLE,
  SEGMENT_COUNT,
  ULTRA_WIDE_PORTRAIT_HFOV,
  WIDE_PORTRAIT_HFOV,
  makeSegments,
} from '../models/types';
import { HapticFeedback } from '../utils/haptics';
import { api } from '../services/api';
import CaptureMiniMap from '../components/CaptureMiniMap';
import { LOW_LIGHT_HINT, isLowLight } from '../capture/lowLight';
import {
  AIM_HOLD_MS,
  AIM_TOLERANCE_DEG,
  CameraBasis,
  MANUAL_TOLERANCE_DEG,
  Quat,
  STEADY_MAX_DEG_PER_SEC,
  TargetProjection,
  basisFromQuat,
  cameraBasis,
  edgePoint,
  headingFromLook,
  nearestTarget,
  projectTargets,
  quatFromAttitude,
  rotationRateDegPerSec,
  slerpQuat,
  wrap360,
} from '../capture/orientation';

type Props = NativeStackScreenProps<RootStackParamList, 'Capture'>;

type Pose = {
  /** Unsmoothed optical heading. Origin and per-shot yaws use this, not Euler yaw. */
  headingDeg: number;
  /** Smoothed basis used to draw the dots. */
  basis: CameraBasis;
  rate: number;
};

type Frame = {
  dots: TargetProjection[];
  captured: boolean[];
  suggested: number;
  alignDeg: number;
  aim: number;
  headingDeg: number;
  tilted: boolean;
  /** Aimed at an open dot but turning faster than STEADY_MAX_DEG_PER_SEC. */
  fast: boolean;
  /** Elevation of the camera look vector, degrees (+ up). */
  elevationDeg: number;
  /** Roll of the phone around the look axis, degrees. */
  rollDeg: number;
  edge: { x: number; y: number; angle: number } | null;
};

const RETICLE = 88;
const RING_R = 34;
const RING = 2 * Math.PI * RING_R;
/** Same threshold auto-capture uses; the manual shutter now honours it too. */
const TILT_LIMIT_DEG = 18;
/** Horizon cue turns green inside this band. Tune on device. */
const LEVEL_OK_DEG = 5;
const SENSOR_WATCHDOG_MS = 1500;
const AUTO_REVIEW_MS = 600;
const FAILURES_BEFORE_RESTART_HINT = 3;

type MotionState = 'checking' | 'ok' | 'unavailable' | 'denied' | 'stalled';

export default function CaptureScreen({ navigation, route }: Props) {
  const { projectId, projectName, retakeIndex, retakeNonce } = route.params;
  const insets = useSafeAreaInsets();
  const focused = useIsFocused();
  const { width, height } = useWindowDimensions();
  const [permission, requestPermission] = useCameraPermissions();
  const [segments, setSegments] = useState<CaptureSegment[]>(makeSegments());
  const [selectedLens, setSelectedLens] = useState<string | undefined>();
  const [hfov, setHfov] = useState(ULTRA_WIDE_PORTRAIT_HFOV);
  const [capturing, setCapturing] = useState(false);
  const [locked, setLocked] = useState(false);
  const [reduceMotion, setReduceMotion] = useState(false);
  const [frame, setFrame] = useState<Frame | null>(null);
  const [motion, setMotion] = useState<MotionState>('checking');
  /** Person chose to continue without motion sensors: manual shutter with fixed yaws. */
  const [manualOnly, setManualOnly] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const [lastShot, setLastShot] = useState<number | null>(null);
  const [showTurnHint, setShowTurnHint] = useState(false);
  const [completeFlash, setCompleteFlash] = useState(false);
  const toastTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const failuresRef = useRef(0);
  const manualOnlyRef = useRef(false);
  const wasFillingRef = useRef(false);
  const completedRef = useRef(false);
  const leavingRef = useRef(false);
  const cameraRef = useRef<CameraView>(null);
  const segmentsRef = useRef(segments);
  const originRef = useRef<number | null>(null);
  const poseRef = useRef<Pose | null>(null);
  const smoothRef = useRef<Quat | null>(null);
  const aimMsRef = useRef(0);
  const captureLock = useRef(false);
  const frameRef = useRef<Frame | null>(null);
  const hfovRef = useRef(hfov);
  const reduceRef = useRef(reduceMotion);
  const sizeRef = useRef({ width, height });
  const focusedRef = useRef(focused);

  useEffect(() => {
    segmentsRef.current = segments;
  }, [segments]);
  useEffect(() => {
    manualOnlyRef.current = manualOnly;
  }, [manualOnly]);
  useEffect(() => () => {
    if (toastTimer.current) clearTimeout(toastTimer.current);
  }, []);

  const showToast = useCallback((message: string, ms = 2600) => {
    if (toastTimer.current) clearTimeout(toastTimer.current);
    setToast(message);
    AccessibilityInfo.announceForAccessibility(message);
    toastTimer.current = setTimeout(() => setToast(null), ms);
  }, []);

  // P0-1: check motion availability and permission before relying on it.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const available = await DeviceMotion.isAvailableAsync();
        if (cancelled) return;
        if (!available) {
          setMotion('unavailable');
          return;
        }
        let perm = await DeviceMotion.getPermissionsAsync();
        if (!perm.granted && perm.canAskAgain) perm = await DeviceMotion.requestPermissionsAsync();
        if (cancelled) return;
        setMotion(perm.granted ? 'ok' : 'denied');
      } catch {
        if (!cancelled) setMotion('unavailable');
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // P0-2: confirm before a back gesture or button throws photos away.
  useEffect(() => {
    const unsub = navigation.addListener('beforeRemove', (e) => {
      const count = segmentsRef.current.filter((seg) => seg.imageUri !== null).length;
      const type = e.data.action.type;
      const userBack = type === 'GO_BACK' || type === 'POP';
      if (count === 0 || !userBack || leavingRef.current) return;
      e.preventDefault();
      Alert.alert(
        `Discard ${count} photo${count === 1 ? '' : 's'}?`,
        'Your photos for this room are only on this screen. Leaving now throws them away.',
        [
          { text: 'Keep capturing', style: 'cancel' },
          {
            text: 'Discard photos',
            style: 'destructive',
            onPress: () => {
              leavingRef.current = true;
              navigation.dispatch(e.data.action);
            },
          },
          {
            // P0-11: don't leave a ghost "0 photos" room behind.
            text: 'Discard and delete room',
            style: 'destructive',
            onPress: () => {
              leavingRef.current = true;
              api.deleteProject(projectId).catch(() => {
                // The row stays as "Not captured yet" and can be deleted from Home.
              });
              navigation.dispatch(e.data.action);
            },
          },
        ],
      );
    });
    return unsub;
  }, [navigation, projectId]);

  // P0-3: Review asked to retake a photo. Clear it and point at its dot.
  useEffect(() => {
    if (retakeIndex == null || retakeIndex < 0 || retakeIndex >= SEGMENT_COUNT) return;
    completedRef.current = false;
    setCompleteFlash(false);
    setSegments((prev) => {
      const updated = [...prev];
      updated[retakeIndex] = { ...updated[retakeIndex], imageUri: null, actualYaw: null, capturedAt: null };
      return updated;
    });
    showToast(`Aim at dot ${retakeIndex + 1} to retake it`);
  }, [retakeIndex, retakeNonce, showToast]);
  useEffect(() => {
    hfovRef.current = hfov;
  }, [hfov]);
  useEffect(() => {
    reduceRef.current = reduceMotion;
  }, [reduceMotion]);
  useEffect(() => {
    sizeRef.current = { width, height };
  }, [width, height]);
  useEffect(() => {
    focusedRef.current = focused;
    if (!focused) aimMsRef.current = 0;
  }, [focused]);

  useEffect(() => {
    let mounted = true;
    AccessibilityInfo.isReduceMotionEnabled().then((enabled) => {
      if (mounted) setReduceMotion(enabled);
    });
    const sub = AccessibilityInfo.addEventListener('reduceMotionChanged', setReduceMotion);
    return () => {
      mounted = false;
      sub.remove();
    };
  }, []);

  const captureAt = useCallback(async (index: number) => {
    if (captureLock.current || !cameraRef.current) return;
    if (index < 0 || index >= SEGMENT_COUNT) return;
    const pose = poseRef.current;
    // Without motion data only the manual fallback may shoot, using fixed yaws.
    if (!pose && !manualOnlyRef.current) return;
    captureLock.current = true;
    aimMsRef.current = 0;
    setCapturing(true);
    const headingAtShutter = pose ? pose.headingDeg : null;
    try {
      const photo = await cameraRef.current.takePictureAsync({
        quality: 0.8,
        skipProcessing: false,
        exif: true,
      });
      if (!photo?.uri) throw new Error('No photo returned');
      let actualYaw: number;
      if (headingAtShutter == null) {
        actualYaw = index * SEGMENT_ANGLE;
      } else {
        if (originRef.current == null) originRef.current = headingAtShutter;
        actualYaw = wrap360(headingAtShutter - originRef.current);
      }
      const firstShot = !segmentsRef.current.some((seg) => seg.imageUri !== null);
      setLocked(true);
      setSegments((prev) => {
        const updated = [...prev];
        updated[index] = {
          ...updated[index],
          imageUri: photo.uri,
          actualYaw,
          capturedAt: new Date(),
        };
        return updated;
      });
      HapticFeedback.capture();
      failuresRef.current = 0;
      setLastShot(index);
      if (firstShot) setShowTurnHint(true);
      const done = segmentsRef.current.filter((seg) => seg.imageUri !== null).length + 1;
      AccessibilityInfo.announceForAccessibility(`Photo ${Math.min(done, SEGMENT_COUNT)} of ${SEGMENT_COUNT}`);
      // P1-16: a gentle hint only. The photo is kept and capture carries on;
      // the existing Retake last button lets the user redo it.
      if (isLowLight(photo.exif as Record<string, unknown> | undefined)) {
        showToast(LOW_LIGHT_HINT, 4000);
      }
    } catch (e) {
      if (__DEV__) console.warn('Capture error:', e);
      HapticFeedback.error();
      failuresRef.current += 1;
      if (failuresRef.current >= FAILURES_BEFORE_RESTART_HINT) {
        showToast("Photos keep failing. Go back and open the capture again to restart the camera.", 4000);
      } else {
        showToast("Photo didn't save, hold steady and try again");
      }
    } finally {
      captureLock.current = false;
      setCapturing(false);
    }
  }, [showToast]);

  const captureRef = useRef(captureAt);
  captureRef.current = captureAt;

  useEffect(() => {
    if (!permission?.granted || (motion !== 'ok' && motion !== 'stalled')) return;
    DeviceMotion.setUpdateInterval(33);
    const watchdog = setTimeout(() => {
      if (!poseRef.current) setMotion('stalled');
    }, SENSOR_WATCHDOG_MS);
    const sub = DeviceMotion.addListener((data: DeviceMotionMeasurement) => {
      if (!data.rotation) return;
      if (!poseRef.current) setMotion((m) => (m === 'stalled' ? 'ok' : m));
      const yawRad = data.rotation.alpha;
      const pitchRad = data.rotation.beta;
      const rollRad = data.rotation.gamma;
      const raw = cameraBasis(yawRad, pitchRad, rollRad);
      const sample = quatFromAttitude(yawRad, pitchRad, rollRad);
      const previous = smoothRef.current;
      const smooth = previous
        ? slerpQuat(previous, sample, reduceRef.current ? 0.18 : 0.42)
        : sample;
      smoothRef.current = smooth;
      poseRef.current = {
        headingDeg: headingFromLook(raw.look),
        basis: basisFromQuat(smooth),
        rate: rotationRateDegPerSec(data.rotationRate),
      };
    });

    let raf = 0;
    let last = 0;
    const tick = (now: number) => {
      raf = requestAnimationFrame(tick);
      const minGap = reduceRef.current ? 100 : 50;
      if (now - last < minGap) return;
      const dt = last === 0 ? minGap : now - last;
      last = now;
      const pose = poseRef.current;
      if (!pose || !focusedRef.current) return;
      const { width: viewW, height: viewH } = sizeRef.current;
      if (viewW < 2 || viewH < 2) return;
      const captured = segmentsRef.current.map((segment) => segment.imageUri !== null);
      const origin = originRef.current;
      let dots: TargetProjection[] = [];
      let suggested = -1;
      let alignDeg = 180;
      let headingDeg = 0;
      if (origin == null) {
        suggested = 0;
        alignDeg = 0;
        headingDeg = 0;
        dots = [
          {
            index: 0,
            yawDeg: 0,
            inView: true,
            x: viewW / 2,
            y: viewH / 2,
            planeX: 0,
            planeY: -1,
            angleDeg: 0,
          },
        ];
      } else {
        dots = projectTargets(pose.basis, origin, viewW, viewH, hfovRef.current);
        headingDeg = wrap360(headingFromLook(pose.basis.look) - origin);
        const next = nearestTarget(dots, captured);
        if (next) {
          suggested = next.index;
          alignDeg = next.angleDeg;
        }
      }
      const elevation = Math.asin(Math.min(1, Math.max(-1, pose.basis.look.z))) * (180 / Math.PI);
      const tilted = Math.abs(elevation) > TILT_LIMIT_DEG;
      // Roll: how far the phone's horizontal axis dips out of the horizon.
      const rollDeg = Math.asin(Math.min(1, Math.max(-1, pose.basis.right.z))) * (180 / Math.PI);
      const open = suggested >= 0 && !captured[suggested];
      const aimed = open && alignDeg <= AIM_TOLERANCE_DEG && !tilted;
      const steady = pose.rate <= STEADY_MAX_DEG_PER_SEC;
      if (aimed && steady && !captureLock.current) aimMsRef.current += dt;
      else aimMsRef.current = 0;
      const aim = Math.min(1, aimMsRef.current / AIM_HOLD_MS);
      const filling = aim > 0;
      if (filling && !wasFillingRef.current) void HapticFeedback.light();
      wasFillingRef.current = filling;
      if (aim >= 1 && open) {
        aimMsRef.current = 0;
        void captureRef.current(suggested);
      }
      const suggestedDot = dots.find((dot) => dot.index === suggested);
      let edge: Frame['edge'] = null;
      if (suggestedDot && !suggestedDot.inView) {
        const offX = suggestedDot.x - viewW / 2;
        const offY = suggestedDot.y - viewH / 2;
        const behind = Math.hypot(offX, offY) < 1;
        edge = edgePoint(
          behind ? suggestedDot.planeX : offX,
          behind ? suggestedDot.planeY : offY,
          viewW,
          viewH,
          { top: 150, right: 28, bottom: 210, left: 28 },
        );
      }
      const nextFrame: Frame = {
        dots,
        captured,
        suggested,
        alignDeg,
        aim: reduceRef.current ? (aim > 0 ? 1 : 0) : aim,
        headingDeg,
        tilted,
        fast: aimed && !steady,
        elevationDeg: elevation,
        rollDeg,
        edge,
      };
      frameRef.current = nextFrame;
      setFrame(nextFrame);
    };
    raf = requestAnimationFrame(tick);
    return () => {
      clearTimeout(watchdog);
      cancelAnimationFrame(raf);
      sub.remove();
    };
  }, [permission?.granted, motion === 'ok' || motion === 'stalled']);

  const handleManual = useCallback(() => {
    if (manualOnlyRef.current && !poseRef.current) {
      // Sensor-free fallback: shoot the next open slot at its nominal yaw.
      const next = segmentsRef.current.findIndex((seg) => seg.imageUri === null);
      if (next >= 0) void captureRef.current(next);
      return;
    }
    const current = frameRef.current;
    // Same tilt rule as auto-capture: a tilted shot breaks the stitch.
    if (current?.tilted) {
      HapticFeedback.error();
      showToast(`Level the phone first. It's tilted more than ${TILT_LIMIT_DEG}°.`);
      return;
    }
    if (originRef.current == null) {
      void captureRef.current(0);
      return;
    }
    if (!current) return;
    let openDot: TargetProjection | null = null;
    let filledDot: TargetProjection | null = null;
    for (const dot of current.dots) {
      if (current.captured[dot.index]) {
        if (!filledDot || dot.angleDeg < filledDot.angleDeg) filledDot = dot;
      } else if (!openDot || dot.angleDeg < openDot.angleDeg) {
        openDot = dot;
      }
    }
    if (openDot && openDot.angleDeg <= MANUAL_TOLERANCE_DEG) {
      void captureRef.current(openDot.index);
      return;
    }
    if (filledDot && filledDot.angleDeg <= AIM_TOLERANCE_DEG) {
      void captureRef.current(filledDot.index);
      return;
    }
    showToast(openDot ? 'Turn to the highlighted dot first' : 'Aim at a dot');
  }, [showToast]);

  const handleRetakeLast = useCallback(() => {
    if (lastShot == null) return;
    completedRef.current = false;
    setCompleteFlash(false);
    setSegments((prev) => {
      const updated = [...prev];
      updated[lastShot] = { ...updated[lastShot], imageUri: null, actualYaw: null, capturedAt: null };
      return updated;
    });
    showToast(`Photo ${lastShot + 1} removed. Aim at its dot again.`);
    setLastShot(null);
  }, [lastShot, showToast]);

  const goToReview = useCallback(() => {
    navigation.navigate('Review', {
      projectId,
      projectName,
      hfov: hfovRef.current,
      lens: selectedLens,
      segments: segmentsRef.current.map((segment) => ({
        id: segment.id,
        imageUri: segment.imageUri,
        actualYaw: segment.actualYaw,
      })),
    });
  }, [navigation, projectId, projectName, selectedLens]);
  const goToReviewRef = useRef(goToReview);
  goToReviewRef.current = goToReview;

  const capturedTotal = segments.filter((segment) => segment.imageUri !== null).length;

  // P0-5: celebrate and move on automatically at 16/16.
  useEffect(() => {
    if (capturedTotal !== SEGMENT_COUNT || completedRef.current) return;
    completedRef.current = true;
    HapticFeedback.success();
    setCompleteFlash(true);
    AccessibilityInfo.announceForAccessibility('All 16 photos captured. Opening review.');
    const timer = setTimeout(() => {
      if (focusedRef.current) goToReviewRef.current();
    }, AUTO_REVIEW_MS);
    return () => clearTimeout(timer);
  }, [capturedTotal]);

  useEffect(() => {
    if (!showTurnHint) return;
    const timer = setTimeout(() => setShowTurnHint(false), 3500);
    return () => clearTimeout(timer);
  }, [showTurnHint]);

  const handleLenses = useCallback((event: { lenses: string[] }) => {
    const available = event.lenses ?? [];
    const ultra = available.find((name) => /ultra/i.test(name));
    if (ultra) {
      setSelectedLens(ultra);
      setHfov(ULTRA_WIDE_PORTRAIT_HFOV);
      return;
    }
    const wide = available.find((name) => /wide/i.test(name));
    if (wide) {
      setSelectedLens(wide);
      setHfov(WIDE_PORTRAIT_HFOV);
      return;
    }
    setHfov(ULTRA_WIDE_PORTRAIT_HFOV);
  }, []);

  if (!permission) {
    return (
      <View style={styles.centered}>
        <Text style={styles.permissionText}>Requesting camera permission...</Text>
      </View>
    );
  }

  if (!permission.granted) {
    return (
      <View style={styles.centered}>
        <Text style={styles.permissionText}>Camera access is required</Text>
        <Text style={styles.permissionSubtext}>
          360Flow needs your camera to capture room photos
        </Text>
        <TouchableOpacity
          style={styles.grantButton}
          onPress={permission.canAskAgain ? requestPermission : () => Linking.openSettings()}
          accessibilityRole="button"
        >
          <Text style={styles.grantButtonText}>{permission.canAskAgain ? 'Allow camera' : 'Open Settings'}</Text>
        </TouchableOpacity>
        <BackLink onPress={() => navigation.goBack()} />
      </View>
    );
  }

  if ((motion === 'unavailable' || motion === 'denied') && !manualOnly) {
    return (
      <View style={styles.centered}>
        <Text style={styles.permissionText} accessibilityRole="header">
          {motion === 'denied' ? 'Motion access is off' : 'Motion sensors unavailable'}
        </Text>
        <Text style={styles.permissionSubtext}>
          360Flow uses motion to place the dots and shoot automatically.
          {motion === 'denied' ? ' Turn on Motion & Fitness for 360Flow in Settings.' : ''}
        </Text>
        {motion === 'denied' ? (
          <TouchableOpacity style={styles.grantButton} onPress={() => Linking.openSettings()} accessibilityRole="button">
            <Text style={styles.grantButtonText}>Open Settings</Text>
          </TouchableOpacity>
        ) : null}
        <TouchableOpacity
          style={styles.secondaryButton}
          onPress={() => setManualOnly(true)}
          accessibilityRole="button"
          accessibilityHint="Take 16 photos with the shutter, turning about one sixteenth of a circle each time"
        >
          <Text style={styles.secondaryButtonText}>Continue with manual shutter</Text>
        </TouchableOpacity>
        <BackLink onPress={() => navigation.goBack()} />
      </View>
    );
  }

  const capturedCount = capturedTotal;
  const isComplete = capturedCount === SEGMENT_COUNT;
  const progressPct = (capturedCount / SEGMENT_COUNT) * 100;
  const capturedFlags = frame?.captured ?? segments.map((segment) => segment.imageUri !== null);
  const sensorless = manualOnly && !poseRef.current;
  const hint = sensorless
    ? `Turn a little right after each photo, then tap the shutter (${capturedCount}/${SEGMENT_COUNT})`
    : hintText(isComplete, locked, frame, capturing, showTurnHint);
  const ringColor = completeFlash ? '#4CAF50' : frame?.fast ? '#FFB300' : '#7CFFB2';
  const levelOk =
    frame != null && Math.abs(frame.rollDeg) <= LEVEL_OK_DEG && Math.abs(frame.elevationDeg) <= LEVEL_OK_DEG;
  const horizonColor = levelOk ? '#4CAF50' : frame?.tilted ? '#FFD54F' : 'rgba(255,255,255,0.8)';
  const bubbleY = frame ? Math.max(-26, Math.min(26, -frame.elevationDeg * 1.4)) : 0;

  return (
    <View style={styles.container}>
      <CameraView
        ref={cameraRef}
        style={StyleSheet.absoluteFill}
        facing="back"
        selectedLens={selectedLens}
        onAvailableLensesChanged={handleLenses}
      />

      <View style={styles.overlay} pointerEvents="box-none">
        <View>
          <View style={[styles.topBar, { paddingTop: insets.top + 8 }]}>
            <TouchableOpacity
              onPress={() => navigation.goBack()}
              style={styles.hit}
              hitSlop={12}
              accessibilityRole="button"
              accessibilityLabel="Back"
            >
              <Text style={styles.backText}>← Back</Text>
            </TouchableOpacity>
            <Text style={styles.roomName} numberOfLines={1} maxFontSizeMultiplier={1.4}>{projectName}</Text>
            <View style={styles.topRight}>
              <Text
                style={styles.countText}
                maxFontSizeMultiplier={1.4}
                accessibilityLabel={`${capturedCount} of ${SEGMENT_COUNT} photos`}
              >
                {capturedCount}/{SEGMENT_COUNT}
              </Text>
              <TouchableOpacity
                onPress={() => navigation.navigate('CaptureCoach')}
                style={styles.tipsButton}
                hitSlop={8}
                accessibilityRole="button"
                accessibilityLabel="Capture tips"
              >
                <Text style={styles.tipsText}>?</Text>
              </TouchableOpacity>
            </View>
          </View>
          <View style={styles.progressTrack}>
            <View style={[styles.progressFill, { width: `${progressPct}%` }]} />
          </View>
          {__DEV__ ? (
            <Text style={styles.lensText}>
              {selectedLens ? `${selectedLens} · ${hfov}°` : `Ultra-wide · ${hfov}°`}
            </Text>
          ) : null}
          {motion === 'stalled' ? (
            <View style={styles.banner} accessibilityRole="alert">
              <Text style={styles.bannerText}>Sensors not responding. Auto-capture is off.</Text>
              {!manualOnly ? (
                <TouchableOpacity onPress={() => setManualOnly(true)} style={styles.hit} accessibilityRole="button">
                  <Text style={styles.bannerAction}>Use manual shutter</Text>
                </TouchableOpacity>
              ) : null}
            </View>
          ) : null}
          {frame?.tilted ? (
            <Text style={styles.tiltWarning} maxFontSizeMultiplier={1.4} accessibilityRole="alert">
              Hold the phone upright — tilt is too high
            </Text>
          ) : null}
        </View>

        <View
          style={StyleSheet.absoluteFill}
          pointerEvents="none"
          accessibilityElementsHidden
          importantForAccessibility="no-hide-descendants"
        >
          {frame?.dots.map((dot) => {
            if (!dot.inView) return null;
            const done = capturedFlags[dot.index];
            const active = dot.index === frame.suggested && !done;
            const size = active ? 22 : 16;
            return (
              <View
                key={dot.index}
                style={[
                  styles.dot,
                  done ? styles.dotDone : active ? styles.dotNext : styles.dotOpen,
                  { width: size, height: size, borderRadius: size / 2, left: dot.x - size / 2, top: dot.y - size / 2 },
                ]}
              >
                {done ? <Text style={styles.dotCheck}>✓</Text> : null}
              </View>
            );
          })}
          <Svg
            width={RETICLE}
            height={RETICLE}
            style={[styles.reticle, { left: width / 2 - RETICLE / 2, top: height / 2 - RETICLE / 2 }]}
          >
            <Circle cx={RETICLE / 2} cy={RETICLE / 2} r={8} stroke="#fff" strokeWidth={2} fill="none" />
            <Circle cx={RETICLE / 2} cy={RETICLE / 2} r={2} fill="#fff" />
            {frame && !sensorless ? (
              <Line
                x1={RETICLE / 2 - 22}
                y1={RETICLE / 2 + bubbleY}
                x2={RETICLE / 2 + 22}
                y2={RETICLE / 2 + bubbleY}
                stroke={horizonColor}
                strokeWidth={2}
                strokeLinecap="round"
                transform={`rotate(${-frame.rollDeg} ${RETICLE / 2} ${RETICLE / 2})`}
              />
            ) : null}
            {completeFlash ? (
              <Circle cx={RETICLE / 2} cy={RETICLE / 2} r={RING_R} stroke={ringColor} strokeWidth={5} fill="none" />
            ) : frame && frame.fast ? (
              <Circle cx={RETICLE / 2} cy={RETICLE / 2} r={RING_R} stroke={ringColor} strokeWidth={4} fill="none" />
            ) : frame && frame.aim > 0 && !reduceMotion ? (
              <Circle
                cx={RETICLE / 2}
                cy={RETICLE / 2}
                r={RING_R}
                stroke={ringColor}
                strokeWidth={4}
                fill="none"
                strokeDasharray={`${RING} ${RING}`}
                strokeDashoffset={RING * (1 - frame.aim)}
                strokeLinecap="round"
                transform={`rotate(-90 ${RETICLE / 2} ${RETICLE / 2})`}
              />
            ) : null}
          </Svg>
          {frame?.edge ? (
            <View style={[styles.edgeChip, { left: frame.edge.x - 16, top: frame.edge.y - 16 }]}>
              <Text style={[styles.edgeArrow, { transform: [{ rotate: `${frame.edge.angle}rad` }] }]}>›</Text>
            </View>
          ) : null}
        </View>

        {toast ? (
          <View style={styles.toast} pointerEvents="none" accessibilityLiveRegion="polite">
            <Text style={styles.toastText} maxFontSizeMultiplier={1.4}>{toast}</Text>
          </View>
        ) : null}

        <View style={[styles.bottomBar, { paddingBottom: insets.bottom + 20 }]} pointerEvents="box-none">
          <View accessible accessibilityLabel={`Map: ${capturedCount} of ${SEGMENT_COUNT} photos done`}>
            <CaptureMiniMap
              captured={capturedFlags}
              headingDeg={frame?.headingDeg ?? 0}
              suggested={frame?.suggested ?? 0}
            />
          </View>
          <View style={styles.shutterRow}>
            <View style={styles.sideSlot}>
              {lastShot != null && !isComplete ? (
                <TouchableOpacity
                  style={styles.sideButton}
                  onPress={handleRetakeLast}
                  accessibilityRole="button"
                  accessibilityLabel={`Retake last photo, number ${lastShot + 1}`}
                >
                  <Text style={styles.sideButtonText}>↺ Retake last</Text>
                </TouchableOpacity>
              ) : null}
            </View>
            {isComplete ? (
              <TouchableOpacity style={styles.reviewButton} onPress={goToReview} accessibilityRole="button">
                <Text style={styles.reviewButtonText}>Check photos →</Text>
              </TouchableOpacity>
            ) : (
              <TouchableOpacity
                style={[styles.captureButton, capturing && styles.captureButtonCapturing]}
                onPress={handleManual}
                disabled={capturing}
                activeOpacity={0.7}
                accessibilityRole="button"
                accessibilityLabel={`Take photo manually, ${capturedCount} of ${SEGMENT_COUNT} done`}
                accessibilityState={{ disabled: capturing, busy: capturing }}
              >
                <View style={[styles.captureButtonInner, capturing && styles.captureButtonInnerCapturing]} />
              </TouchableOpacity>
            )}
            <View style={styles.sideSlot} />
          </View>
          <Text style={styles.hintText} maxFontSizeMultiplier={1.4} accessibilityLiveRegion="polite">
            {hint}
          </Text>
        </View>
      </View>
    </View>
  );
}

function BackLink({ onPress }: { onPress: () => void }) {
  return (
    <TouchableOpacity onPress={onPress} style={[styles.hit, { marginTop: 16 }]} accessibilityRole="button">
      <Text style={styles.backLinkText}>Back</Text>
    </TouchableOpacity>
  );
}

function hintText(
  complete: boolean,
  locked: boolean,
  frame: Frame | null,
  capturing: boolean,
  turnHint: boolean,
): string {
  if (complete) return 'All 16 photos captured!';
  if (capturing) return 'Capturing…';
  if (frame?.tilted) return 'Level the phone, then aim at a dot';
  if (frame?.fast) return 'Slow down, hold still';
  if (!locked) return 'Face the most interesting wall, then hold still';
  if (turnHint) return 'Turn right ➜';
  if (frame && frame.suggested >= 0 && frame.alignDeg <= AIM_TOLERANCE_DEG) {
    return frame.aim > 0 ? 'Hold steady' : 'Tap to capture, or hold steady';
  }
  if (frame?.edge) return `${Math.round(frame.alignDeg)}° to the next dot`;
  return 'Aim the circle at a dot';
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#000',
  },
  overlay: {
    ...StyleSheet.absoluteFill,
    justifyContent: 'space-between',
  },
  centered: {
    flex: 1,
    backgroundColor: '#1a1a2e',
    justifyContent: 'center',
    alignItems: 'center',
    padding: 40,
  },
  permissionText: {
    fontSize: 18,
    fontWeight: '600',
    color: '#fff',
    marginBottom: 8,
    textAlign: 'center',
  },
  permissionSubtext: {
    fontSize: 14,
    color: '#888',
    textAlign: 'center',
    marginBottom: 24,
  },
  grantButton: {
    backgroundColor: '#6C63FF',
    paddingHorizontal: 24,
    paddingVertical: 14,
    borderRadius: 12,
  },
  grantButtonText: {
    color: '#fff',
    fontWeight: '600',
    fontSize: 16,
  },
  topBar: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingHorizontal: 16,
    paddingBottom: 8,
    gap: 8,
  },
  hit: {
    minHeight: 44,
    minWidth: 44,
    justifyContent: 'center',
  },
  topRight: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
  },
  tipsButton: {
    width: 44,
    height: 44,
    borderRadius: 22,
    alignItems: 'center',
    justifyContent: 'center',
  },
  tipsText: {
    color: '#fff',
    fontSize: 16,
    fontWeight: '700',
    width: 28,
    height: 28,
    lineHeight: 26,
    borderRadius: 14,
    borderWidth: 1.5,
    borderColor: 'rgba(255,255,255,0.8)',
    textAlign: 'center',
    overflow: 'hidden',
  },
  banner: {
    marginHorizontal: 16,
    marginTop: 8,
    padding: 10,
    borderRadius: 10,
    backgroundColor: 'rgba(244,67,54,0.85)',
    alignItems: 'center',
  },
  bannerText: { color: '#fff', fontWeight: '700', textAlign: 'center' },
  bannerAction: { color: '#fff', fontWeight: '700', textDecorationLine: 'underline' },
  toast: {
    position: 'absolute',
    left: 24,
    right: 24,
    top: '58%',
    backgroundColor: 'rgba(0,0,0,0.78)',
    borderRadius: 12,
    padding: 12,
  },
  toastText: { color: '#fff', fontWeight: '600', textAlign: 'center', fontSize: 15 },
  shutterRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    alignSelf: 'stretch',
  },
  sideSlot: { flex: 1, alignItems: 'center' },
  sideButton: {
    minHeight: 44,
    paddingHorizontal: 12,
    justifyContent: 'center',
    borderRadius: 22,
    backgroundColor: 'rgba(0,0,0,0.5)',
  },
  sideButtonText: { color: '#fff', fontWeight: '600', fontSize: 14 },
  secondaryButton: {
    marginTop: 12,
    minHeight: 48,
    paddingHorizontal: 20,
    justifyContent: 'center',
    borderRadius: 12,
    borderWidth: 1,
    borderColor: '#3d3866',
  },
  secondaryButtonText: { color: '#fff', fontWeight: '600', fontSize: 16 },
  backLinkText: { color: '#8F88FF', fontSize: 16, fontWeight: '600', textAlign: 'center' },
  backText: {
    color: '#fff',
    fontSize: 16,
    fontWeight: '500',
  },
  roomName: {
    color: '#fff',
    fontSize: 16,
    fontWeight: '600',
  },
  countText: {
    color: 'rgba(255,255,255,0.8)',
    fontSize: 15,
    fontWeight: '600',
  },
  progressTrack: {
    height: 4,
    backgroundColor: 'rgba(255,255,255,0.15)',
    marginHorizontal: 20,
    borderRadius: 2,
    overflow: 'hidden',
  },
  progressFill: {
    height: '100%',
    backgroundColor: '#6C63FF',
    borderRadius: 2,
  },
  lensText: {
    color: 'rgba(255,255,255,0.75)',
    fontSize: 12,
    textAlign: 'center',
    marginTop: 8,
  },
  tiltWarning: {
    color: '#FFD54F',
    fontSize: 13,
    fontWeight: '600',
    textAlign: 'center',
    marginTop: 6,
    paddingHorizontal: 20,
  },
  dot: {
    position: 'absolute',
    alignItems: 'center',
    justifyContent: 'center',
    borderWidth: 2,
  },
  dotOpen: {
    backgroundColor: 'rgba(255,255,255,0.2)',
    borderColor: '#fff',
  },
  dotNext: {
    backgroundColor: 'rgba(0,229,255,0.35)',
    borderColor: '#00E5FF',
  },
  dotDone: {
    backgroundColor: 'rgba(76,175,80,0.85)',
    borderColor: '#4CAF50',
  },
  dotCheck: {
    color: '#fff',
    fontSize: 10,
    fontWeight: '700',
  },
  reticle: {
    position: 'absolute',
  },
  edgeChip: {
    position: 'absolute',
    width: 32,
    height: 32,
    borderRadius: 16,
    backgroundColor: 'rgba(0,0,0,0.55)',
    alignItems: 'center',
    justifyContent: 'center',
  },
  edgeArrow: {
    color: '#00E5FF',
    fontSize: 22,
    fontWeight: '700',
    lineHeight: 24,
  },
  bottomBar: {
    alignItems: 'center',
    gap: 10,
  },
  captureButton: {
    width: 76,
    height: 76,
    borderRadius: 38,
    borderWidth: 4,
    borderColor: 'rgba(255,255,255,0.85)',
    justifyContent: 'center',
    alignItems: 'center',
  },
  captureButtonCapturing: {
    opacity: 0.5,
  },
  captureButtonInner: {
    width: 60,
    height: 60,
    borderRadius: 30,
    backgroundColor: '#fff',
  },
  captureButtonInnerCapturing: {
    backgroundColor: '#888',
  },
  reviewButton: {
    backgroundColor: '#6C63FF',
    paddingHorizontal: 32,
    paddingVertical: 16,
    borderRadius: 14,
  },
  reviewButtonText: {
    color: '#fff',
    fontSize: 16,
    fontWeight: '700',
  },
  hintText: {
    color: 'rgba(255,255,255,0.9)',
    fontSize: 15,
    fontWeight: '600',
    textAlign: 'center',
    paddingHorizontal: 24,
  },
});
