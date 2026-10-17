import React from 'react';
import { View } from 'react-native';
import Svg, { Circle, Line } from 'react-native-svg';
import { SEGMENT_ANGLE, SEGMENT_COUNT } from '../models/types';
import { wrapSigned } from '../capture/orientation';

const SIZE = 104;
const CENTER = SIZE / 2;
const RADIUS = 38;

type Props = {
  captured: boolean[];
  /** Phone heading in the calibrated frame. 0 means the first target is straight ahead. */
  headingDeg: number;
  suggested: number;
};

export default function CaptureMiniMap({ captured, headingDeg, suggested }: Props) {
  return (
    <View pointerEvents="none">
      <Svg width={SIZE} height={SIZE}>
        <Circle cx={CENTER} cy={CENTER} r={RADIUS} stroke="rgba(255,255,255,0.28)" strokeWidth={1} fill="rgba(0,0,0,0.35)" />
        <Line x1={CENTER} y1={CENTER} x2={CENTER} y2={CENTER - RADIUS + 6} stroke="rgba(255,255,255,0.85)" strokeWidth={2} />
        {Array.from({ length: SEGMENT_COUNT }, (_, index) => {
          const delta = wrapSigned(index * SEGMENT_ANGLE - headingDeg);
          const rad = ((delta - 90) * Math.PI) / 180;
          const x = CENTER + RADIUS * Math.cos(rad);
          const y = CENTER + RADIUS * Math.sin(rad);
          const done = captured[index];
          const active = index === suggested;
          const fill = done ? '#4CAF50' : active ? '#00E5FF' : 'rgba(255,255,255,0.55)';
          const r = active ? 5.5 : done ? 4.5 : 3.5;
          return <Circle key={index} cx={x} cy={y} r={r} fill={fill} />;
        })}
      </Svg>
    </View>
  );
}
