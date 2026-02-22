import React from 'react';
import { View, Text, StyleSheet } from 'react-native';
import Svg, { Circle, Path, G } from 'react-native-svg';
import { CaptureSegment, SEGMENT_COUNT, SEGMENT_ANGLE } from '../models/types';

interface Props {
  segments: CaptureSegment[];
  currentYaw: number;
  currentSegmentIndex: number;
  isAligned: boolean;
}

const SIZE = 200;
const CENTER = SIZE / 2;
const RADIUS = 80;
const ARC_WIDTH = 12;

function polarToCartesian(cx: number, cy: number, r: number, angleDeg: number) {
  const rad = ((angleDeg - 90) * Math.PI) / 180;
  return {
    x: cx + r * Math.cos(rad),
    y: cy + r * Math.sin(rad),
  };
}

function describeArc(cx: number, cy: number, r: number, startAngle: number, endAngle: number) {
  const start = polarToCartesian(cx, cy, r, endAngle);
  const end = polarToCartesian(cx, cy, r, startAngle);
  const largeArcFlag = endAngle - startAngle <= 180 ? '0' : '1';
  return `M ${start.x} ${start.y} A ${r} ${r} 0 ${largeArcFlag} 0 ${end.x} ${end.y}`;
}

export default function RotationGuide({ segments, currentYaw, currentSegmentIndex, isAligned }: Props) {
  const capturedCount = segments.filter((s) => s.imageUri !== null).length;

  // Current direction indicator position
  const indicatorAngle = currentYaw;
  const indicator = polarToCartesian(CENTER, CENTER, RADIUS, indicatorAngle);

  return (
    <View style={styles.container}>
      <Svg width={SIZE} height={SIZE} viewBox={`0 0 ${SIZE} ${SIZE}`}>
        {/* Segment arcs */}
        {segments.map((seg, i) => {
          const startAngle = i * SEGMENT_ANGLE + 1;
          const endAngle = (i + 1) * SEGMENT_ANGLE - 1;
          const isCaptured = seg.imageUri !== null;
          const isCurrent = i === currentSegmentIndex;

          let color = '#444'; // uncaptured
          if (isCaptured) color = '#4CAF50'; // captured
          if (isCurrent && !isCaptured) color = isAligned ? '#FFD700' : '#666';

          return (
            <Path
              key={i}
              d={describeArc(CENTER, CENTER, RADIUS, startAngle, endAngle)}
              stroke={color}
              strokeWidth={ARC_WIDTH}
              fill="none"
              strokeLinecap="round"
            />
          );
        })}

        {/* Direction indicator dot */}
        <Circle cx={indicator.x} cy={indicator.y} r={6} fill="#fff" />
      </Svg>

      {/* Center count */}
      <View style={styles.centerLabel}>
        <Text style={styles.countNumber}>{capturedCount}</Text>
        <Text style={styles.countLabel}>of {SEGMENT_COUNT}</Text>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    width: SIZE,
    height: SIZE,
    justifyContent: 'center',
    alignItems: 'center',
  },
  centerLabel: {
    position: 'absolute',
    alignItems: 'center',
  },
  countNumber: {
    fontSize: 32,
    fontWeight: '700',
    color: '#fff',
  },
  countLabel: {
    fontSize: 13,
    color: 'rgba(255,255,255,0.6)',
  },
});
