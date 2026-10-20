import React, { useState, useEffect, useRef, useCallback, memo } from 'react';
import {
  View,
  Text,
  TouchableOpacity,
  StyleSheet,
  AccessibilityInfo,
  Alert,
  Linking,
  Animated,
  useWindowDimensions,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { CameraView, useCameraPermissions } from 'expo-camera';
import { DeviceMotion, DeviceMotionMeasurement } from 'expo-sensors';
import Svg, { Circle } from 'react-native-svg';
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
import { markDeleted } from '../services/deletePolicy';
import CaptureMiniMap from '../components/CaptureMiniMap';
import { LOW_LIGHT_HINT, isLowLight } from '../capture/lowLight';
import {
  CameraBasis,
  TargetProjection,
  edgePoint,
  nearestTarget,
  projectTargets,
  rotationRateDegPerSec,
  wrap360,
} from '../capture/orientation';
import { CAPTURE_TUNING as T } from '../capture/tuning';
import {
  AimTracker,
  OneEuroVec3,
  RateFilter,
  Vec3,
  applyDeadZone,
  basisFromForwardUp,
  deviceAxes,
  manualShotAllowed,
  yawPitchFromForward,
} from '../capture/motionFilter';

type Props = NativeStackScreenProps<RootStackParamList, 'Capture'>;

type Pose = {
  /** Raw (unfiltered) camera axes. Capture decisions and per-shot yaws use these. */
  raw: CameraBasis;
  /** One-euro filtered + dead-zoned axes, used only to draw the overlay. */
  shown: CameraBasis;
  /** Gyro speed, deg/s (unfiltered; the tick low-passes it). */
  rate: number;
  /** Seconds, from the sensor timestamp. */
  t: number;
};

/**
 * Discrete overlay state. Only this goes through React state, and only when a
 * field changes, so sensor ticks never re-render the screen or CameraView.
 * Dot, ring, horizon and edge-chip positions move through Animated values.
 */
type Ui = {
  ready: boolean;
  captured: boolean[];
  suggested: number;
  /** Dots that are in front of the camera and on screen. */
  visible: boolean[];
  locked: boolean;
  holding: boolean;
  tilted: boolean;
  fast: boolean;
  levelOk: boolean;
  edge: boolean;
  /** Angle to the next dot, rounded to 5° for the hint text. */
  alignBucket: number;
  /** Heading for the mini map, rounded to 3°. */
  headingBucket: number;
};

const EMPTY_UI: Ui = {
  ready: false,
  captured: new Array(SEGMENT_COUNT).fill(false),
  suggested: 0,
  visible: new Array(SEGMENT_COUNT).fill(false),
  locked: false,
  holding: false,
  tilted: false,
  fast: false,
  levelOk: false,
  edge: false,
  alignBucket: 0,
  headingBucket: 0,
};

function sameUi(a: Ui, b: Ui): boolean {
  if (
    a.ready !== b.ready ||
    a.suggested !== b.suggested ||
    a.locked !== b.locked ||
    a.holding !== b.holding ||
    a.tilted !== b.tilted ||
    a.fast !== b.fast ||
    a.levelOk !== b.levelOk ||
    a.edge !== b.edge ||
    a.alignBucket !== b.alignBucket ||
    a.headingBucket !== b.headingBucket
  ) {
    return false;
  }
  for (let i = 0; i < SEGMENT_COUNT; i += 1) {
    if (a.captured[i] !== b.captured[i] || a.visible[i] !== b.visible[i]) return false;
  }
  return true;
}

const AnimatedCircle = Animated.createAnimatedComponent(Circle);

/** Never re-renders on sensor ticks: props only change when the lens does. */
const StableCamera = memo(
  React.forwardRef<CameraView, { selectedLens?: string; onAvailableLensesChanged: (e: { lenses: string[] }) => void }>(
    function StableCamera({ selectedLens, onAvailableLensesChanged }, ref) {
      return (
        <CameraView
          ref={ref}
          style={StyleSheet.absoluteFill}
          facing="back"
          selectedLens={selectedLens}
          onAvailableLensesChanged={onAvailableLensesChanged}
        />
      );
    },
  ),
);

const RETICLE = 88;
const RING_R = 34;
const RING = 2 * Math.PI * RING_R;
// Aim, hold, tilt and level thresholds live in src/capture/tuning.ts.
const DOT_SIZE = 22;
const SENSOR_WATCHDOG_MS = 1500;
const AUTO_REVIEW_MS = 600;
const FAILURES_BEFORE_RESTART_HINT = 3;

type MotionState = 'checking' | 'ok' | 'unavailable' | 'denied' | 'stalled';

export default function CaptureScreen({ navigation, route }: Props) {
  const { projectId, projectName, retakeIndex, retakeNonce, clearIndices } = route.params;
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
  const [ui, setUi] = useState<Ui>(EMPTY_UI);
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
  const completedRef = useRef(false);
  const leavingRef = useRef(false);
  const cameraRef = useRef<CameraView>(null);
  const segmentsRef = useRef(segments);
  const originRef = useRef<number | null>(null);
  const poseRef = useRef<Pose | null>(null);
  const aimRef = useRef(new AimTracker());
  const captureLock = useRef(false);
  const uiRef = useRef<Ui>(EMPTY_UI);
  /** Latest raw-basis projections for the manual shutter. */
  const liveRef = useRef<{ dots: TargetProjection[]; suggested: number; pitchDeg: number } | null>(null);
  // Animated values for the overlay: written every frame, never through React state.
  const anim = useRef({
    dots: Array.from({ length: SEGMENT_COUNT }, () => new Animated.ValueXY({ x: -100, y: -100 })),
    ring: new Animated.Value(0),
    horizonY: new Animated.Value(0),
    horizonRot: new Animated.Value(0),
    edge: new Animated.ValueXY({ x: -100, y: -100 }),
    edgeRot: new Animated.Value(0),
  }).current;
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
            onPress: async () => {
              leavingRef.current = true;
              // Wait for the server before going back, so the list Home fetches
              // on return no longer has this room (that race caused the 404).
              try {
                await api.deleteProject(projectId); // a 404 already counts as gone
                markDeleted(projectId);
              } catch {
                // Offline or 5xx: the row stays on Home ("Not captured yet") and can be deleted there.
              }
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
    const clear = new Set([retakeIndex, ...(clearIndices ?? [])]);
    setSegments((prev) =>
      prev.map((seg, i) => (clear.has(i) ? { ...seg, imageUri: null, actualYaw: null, capturedAt: null } : seg)),
    );
    showToast(`Aim at dot ${retakeIndex + 1} to retake it`);
  // eslint-disable-next-line react-hooks/exhaustive-deps
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
    if (!focused) aimRef.current.reset();
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
    aimRef.current.reset();
    setCapturing(true);
    const headingAtShutter = pose ? yawPitchFromForward(pose.raw.look).yawDeg : null;
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
    DeviceMotion.setUpdateInterval(T.sensorIntervalMs);
    const watchdog = setTimeout(() => {
      if (!poseRef.current) setMotion('stalled');
    }, SENSOR_WATCHDOG_MS);
    // Smooth the camera forward and up vectors, never the Euler angles: the
    // rotation matrix is well-defined even where alpha/gamma are not (upright phone).
    const forwardFilter = new OneEuroVec3();
    const upFilter = new OneEuroVec3();
    let shownForward: Vec3 | null = null;
    const sub = DeviceMotion.addListener((data: DeviceMotionMeasurement) => {
      if (!data.rotation) return;
      if (!poseRef.current) setMotion((m) => (m === 'stalled' ? 'ok' : m));
      const axes = deviceAxes(data.rotation.alpha, data.rotation.beta, data.rotation.gamma);
      // JS clock in seconds: sensor timestamp units differ between iOS and Android.
      const t = Date.now() / 1000;
      const smoothForward = forwardFilter.filter(axes.forward, t);
      const smoothUp = upFilter.filter(axes.up, t);
      shownForward = applyDeadZone(shownForward, smoothForward, T.deadZoneDeg);
      poseRef.current = {
        raw: { right: axes.right, up: axes.up, look: axes.forward },
        shown: basisFromForwardUp(shownForward, smoothUp),
        rate: rotationRateDegPerSec(data.rotationRate),
        t,
      };
    });

    const rateFilter = new RateFilter(T.rateSmoothingMs);
    let raf = 0;
    let last = 0;
    let lastSuggested = -1;
    const DEG = 180 / Math.PI;
    const tick = (now: number) => {
      raf = requestAnimationFrame(tick);
      const dt = last === 0 ? 16 : Math.min(100, now - last);
      last = now;
      const pose = poseRef.current;
      if (!pose || !focusedRef.current) return;
      const { width: viewW, height: viewH } = sizeRef.current;
      if (viewW < 2 || viewH < 2) return;
      const captured = segmentsRef.current.map((segment) => segment.imageUri !== null);
      const origin = originRef.current;
      const rate = rateFilter.update(pose.rate, dt);
      const { pitchDeg } = yawPitchFromForward(pose.raw.look);
      const tilted = Math.abs(pitchDeg) > T.tiltLimitDeg;
      const shownPitch = Math.asin(Math.max(-1, Math.min(1, pose.shown.look.z))) * DEG;
      const rollDeg = Math.asin(Math.max(-1, Math.min(1, pose.shown.right.z))) * DEG;

      let suggested = 0;
      let alignDeg = 0;
      let headingDeg = 0;
      const visible = new Array(SEGMENT_COUNT).fill(false);
      let edgeOn = false;
      if (origin == null) {
        // Before the first shot: one dot fixed on the reticle.
        visible[0] = true;
        anim.dots[0].setValue({ x: viewW / 2 - DOT_SIZE / 2, y: viewH / 2 - DOT_SIZE / 2 });
        liveRef.current = { dots: [], suggested: 0, pitchDeg };
      } else {
        // Draw with the smoothed basis; decide with the raw one.
        const shownDots = projectTargets(pose.shown, origin, viewW, viewH, hfovRef.current);
        const rawDots = projectTargets(pose.raw, origin, viewW, viewH, hfovRef.current);
        headingDeg = wrap360(yawPitchFromForward(pose.shown.look).yawDeg - origin);
        const next = nearestTarget(rawDots, captured);
        suggested = next ? next.index : -1;
        alignDeg = next ? next.angleDeg : 180;
        for (const d of shownDots) {
          visible[d.index] = d.inView;
          if (d.inView) anim.dots[d.index].setValue({ x: d.x - DOT_SIZE / 2, y: d.y - DOT_SIZE / 2 });
        }
        const shownNext = suggested >= 0 ? shownDots[suggested] : undefined;
        if (shownNext && !shownNext.inView) {
          const offX = shownNext.x - viewW / 2;
          const offY = shownNext.y - viewH / 2;
          const behind = Math.hypot(offX, offY) < 1;
          const e = edgePoint(
            behind ? shownNext.planeX : offX,
            behind ? shownNext.planeY : offY,
            viewW,
            viewH,
            { top: 150, right: 28, bottom: 210, left: 28 },
          );
          anim.edge.setValue({ x: e.x - 16, y: e.y - 16 });
          anim.edgeRot.setValue(e.angle);
          edgeOn = true;
        }
        liveRef.current = { dots: rawDots, suggested, pitchDeg };
      }

      const open = suggested >= 0 && !captured[suggested];
      const state = aimRef.current.update({
        angleDeg: alignDeg,
        rateDegPerSec: rate,
        dtMs: dt,
        blocked: !open || tilted || captureLock.current,
        targetChanged: suggested !== lastSuggested,
      });
      lastSuggested = suggested;
      if (state.lockedOn) void HapticFeedback.light();
      if (state.fire && open) void captureRef.current(suggested);
      anim.ring.setValue(reduceRef.current ? (state.progress > 0 ? 1 : 0) : state.progress);
      anim.horizonY.setValue(Math.max(-26, Math.min(26, -shownPitch * 1.4)));
      anim.horizonRot.setValue((-rollDeg * Math.PI) / 180);

      const nextUi: Ui = {
        ready: true,
        captured,
        suggested,
        visible,
        locked: state.locked,
        holding: state.progress > 0,
        tilted,
        fast: state.locked && !state.steady,
        levelOk: Math.abs(rollDeg) <= T.levelOkDeg && Math.abs(shownPitch) <= T.levelOkDeg,
        edge: edgeOn,
        alignBucket: Math.round(alignDeg / 5) * 5,
        headingBucket: Math.round(headingDeg / 3) * 3,
      };
      if (!sameUi(uiRef.current, nextUi)) {
        uiRef.current = nextUi;
        setUi(nextUi);
      }
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
    const live = liveRef.current;
    // Same tilt rule as auto-capture: a tilted shot breaks the stitch.
    if (live && Math.abs(live.pitchDeg) > T.tiltLimitDeg) {
      HapticFeedback.error();
      showToast(`Level the phone first. It's tilted more than ${T.tiltLimitDeg}°.`);
      return;
    }
    if (originRef.current == null) {
      void captureRef.current(0);
      return;
    }
    if (!live) return;
    const captured = segmentsRef.current.map((seg) => seg.imageUri !== null);
    const current = live.dots.find((d) => d.index === live.suggested);
    // A shaky hand is never fully blocked: the wider ring around the current dot is enough.
    if (current && !captured[current.index] && manualShotAllowed(current.angleDeg, live.pitchDeg)) {
      void captureRef.current(current.index);
      return;
    }
    // Re-shoot a filled dot only when clearly aimed at it.
    let filled: TargetProjection | null = null;
    for (const d of live.dots) {
      if (captured[d.index] && (!filled || d.angleDeg < filled.angleDeg)) filled = d;
    }
    if (filled && filled.angleDeg <= T.aimEnterDeg) {
      void captureRef.current(filled.index);
      return;
    }
    showToast(current ? 'Turn to the highlighted dot first' : 'Aim at a dot');
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
  const capturedFlags = ui.ready ? ui.captured : segments.map((segment) => segment.imageUri !== null);
  const sensorless = manualOnly && !poseRef.current;
  const hint = sensorless
    ? `Turn a little right after each photo, then tap the shutter (${capturedCount}/${SEGMENT_COUNT})`
    : hintText(isComplete, locked, ui, capturing, showTurnHint);
  const ringColor = completeFlash ? '#4CAF50' : ui.fast ? '#FFB300' : '#7CFFB2';
  const horizonColor = ui.levelOk ? '#4CAF50' : ui.tilted ? '#FFD54F' : 'rgba(255,255,255,0.8)';

  return (
    <View style={styles.container}>
      <StableCamera ref={cameraRef} selectedLens={selectedLens} onAvailableLensesChanged={handleLenses} />

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
          {ui.tilted ? (
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
          {anim.dots.map((position, index) => {
            const show = ui.ready && ui.visible[index] && (originRef.current != null || index === 0);
            const done = capturedFlags[index];
            const active = index === ui.suggested && !done;
            return (
              <Animated.View
                key={index}
                style={[
                  styles.dot,
                  done ? styles.dotDone : active ? styles.dotNext : styles.dotOpen,
                  {
                    opacity: show ? 1 : 0,
                    // Inactive dots draw smaller; scale is in the same transform so it isn't overridden.
                    transform: [...position.getTranslateTransform(), { scale: active ? 1 : 16 / DOT_SIZE }],
                  },
                ]}
              >
                {done ? <Text style={styles.dotCheck}>✓</Text> : null}
              </Animated.View>
            );
          })}
          <View style={[styles.reticle, { left: width / 2 - RETICLE / 2, top: height / 2 - RETICLE / 2 }]}>
            <Svg width={RETICLE} height={RETICLE}>
              <Circle cx={RETICLE / 2} cy={RETICLE / 2} r={8} stroke="#fff" strokeWidth={2} fill="none" />
              <Circle cx={RETICLE / 2} cy={RETICLE / 2} r={2} fill="#fff" />
              {completeFlash ? (
                <Circle cx={RETICLE / 2} cy={RETICLE / 2} r={RING_R} stroke={ringColor} strokeWidth={5} fill="none" />
              ) : ui.locked ? (
                <>
                  {/* Faint full ring while locked on; the bright arc is the hold progress. */}
                  <Circle cx={RETICLE / 2} cy={RETICLE / 2} r={RING_R} stroke={ringColor} strokeOpacity={0.3} strokeWidth={4} fill="none" />
                  <AnimatedCircle
                    cx={RETICLE / 2}
                    cy={RETICLE / 2}
                    r={RING_R}
                    stroke={ringColor}
                    strokeWidth={4}
                    fill="none"
                    strokeDasharray={`${RING} ${RING}`}
                    strokeDashoffset={anim.ring.interpolate({ inputRange: [0, 1], outputRange: [RING, 0] })}
                    strokeLinecap="round"
                    transform={`rotate(-90 ${RETICLE / 2} ${RETICLE / 2})`}
                  />
                </>
              ) : null}
            </Svg>
            {ui.ready && !sensorless ? (
              <Animated.View
                style={[
                  styles.horizon,
                  { backgroundColor: horizonColor },
                  { transform: [{ translateY: anim.horizonY }, { rotate: anim.horizonRot.interpolate({ inputRange: [-Math.PI, Math.PI], outputRange: ['-180deg', '180deg'] }) }] },
                ]}
              />
            ) : null}
          </View>
          {ui.edge ? (
            <Animated.View style={[styles.edgeChip, { left: 0, top: 0, transform: anim.edge.getTranslateTransform() }]}>
              <Animated.Text
                style={[
                  styles.edgeArrow,
                  { transform: [{ rotate: anim.edgeRot.interpolate({ inputRange: [-Math.PI, Math.PI], outputRange: ['-180deg', '180deg'] }) }] },
                ]}
              >
                ›
              </Animated.Text>
            </Animated.View>
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
              headingDeg={ui.headingBucket}
              suggested={ui.suggested}
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
  ui: Ui,
  capturing: boolean,
  turnHint: boolean,
): string {
  if (complete) return 'All 16 photos captured!';
  if (capturing) return 'Capturing…';
  if (ui.tilted) return 'Level the phone, then aim at a dot';
  if (ui.fast) return 'Slow down, hold still';
  if (!locked) return 'Face the most interesting wall, then hold still';
  if (turnHint) return 'Turn right ➜';
  if (ui.locked) return ui.holding ? 'Hold steady' : 'Tap to capture, or hold steady';
  if (ui.edge) return `${ui.alignBucket}° to the next dot`;
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
    left: 0,
    top: 0,
    width: DOT_SIZE,
    height: DOT_SIZE,
    borderRadius: DOT_SIZE / 2,
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
    width: RETICLE,
    height: RETICLE,
    alignItems: 'center',
    justifyContent: 'center',
  },
  horizon: {
    position: 'absolute',
    width: 44,
    height: 2,
    borderRadius: 1,
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
