import React from 'react';
import { Animated, StyleSheet, View } from 'react-native';
import Svg, { Circle, Line } from 'react-native-svg';
import { SEGMENT_ANGLE, SEGMENT_COUNT } from '../models/types';

const SIZE = 104;
const CENTER = SIZE / 2;
const RADIUS = 38;

type Props = {
  captured: boolean[];
  /**
   * Unwrapped phone heading in degrees in the calibrated frame (0 = first target ahead).
   * Animated, so the dial turns every frame without a React re-render.
   */
  heading: Animated.Value;
  suggested: number;
};

export default function CaptureMiniMap({ captured, heading, suggested }: Props) {
  // Dots sit at heading 0; the whole ring rotates by -heading (same as index*angle - heading).
  const rotate = heading.interpolate({
    inputRange: [-360, 360],
    outputRange: ['360deg', '-360deg'],
    extrapolate: 'extend',
  });
  return (
    <View pointerEvents="none" style={{ width: SIZE, height: SIZE }}>
      <Svg width={SIZE} height={SIZE} style={StyleSheet.absoluteFill}>
        <Circle cx={CENTER} cy={CENTER} r={RADIUS} stroke="rgba(255,255,255,0.28)" strokeWidth={1} fill="rgba(0,0,0,0.35)" />
      </Svg>
      <Animated.View style={[StyleSheet.absoluteFill, { transform: [{ rotate }] }]}>
        <Svg width={SIZE} height={SIZE}>
          {Array.from({ length: SEGMENT_COUNT }, (_, index) => {
            const rad = ((index * SEGMENT_ANGLE - 90) * Math.PI) / 180;
            const x = CENTER + RADIUS * Math.cos(rad);
            const y = CENTER + RADIUS * Math.sin(rad);
            const done = captured[index];
            const active = index === suggested;
            const fill = done ? '#4CAF50' : active ? '#00E5FF' : 'rgba(255,255,255,0.55)';
            const r = active ? 5.5 : done ? 4.5 : 3.5;
            return <Circle key={index} cx={x} cy={y} r={r} fill={fill} />;
          })}
        </Svg>
      </Animated.View>
      <Svg width={SIZE} height={SIZE} style={StyleSheet.absoluteFill}>
        <Line x1={CENTER} y1={CENTER} x2={CENTER} y2={CENTER - RADIUS + 6} stroke="rgba(255,255,255,0.85)" strokeWidth={2} />
      </Svg>
    </View>
  );
}
