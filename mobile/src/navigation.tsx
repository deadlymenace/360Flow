import React from 'react';
import { NavigationContainer } from '@react-navigation/native';
import { createNativeStackNavigator } from '@react-navigation/native-stack';

import HomeScreen from './screens/HomeScreen';
import CaptureScreen from './screens/CaptureScreen';
import ReviewScreen from './screens/ReviewScreen';
import ProcessingScreen from './screens/ProcessingScreen';
import PanoramaScreen from './screens/PanoramaScreen';
import PrivacyConsentScreen from './screens/PrivacyConsentScreen';
import WorldScreen from './screens/WorldScreen';
import CaptureCoachScreen from './screens/CaptureCoachScreen';

export type ViewerMode = 'original' | 'staged' | 'compare';

export type ReviewSegment = {
  id: number;
  imageUri: string | null;
  actualYaw: number | null;
  actualPitch?: number | null;
};

export type RootStackParamList = {
  Home: undefined;
  Capture: {
    projectId: string;
    projectName: string;
    /** Set by Review: clear this segment and aim at it again. */
    retakeIndex?: number;
    /** Changes on every retake request so the same index can be retaken twice. */
    retakeNonce?: number;
    /** Photos deleted on Review; cleared here so they get re-shot. */
    clearIndices?: number[];
    /** Changes whenever Review deletes a photo, so Capture re-applies clearIndices. */
    clearNonce?: number;
  };
  /** First-run coach. `next` opens Capture after; without it the coach just closes (Tips). */
  CaptureCoach: { next?: { projectId: string; projectName: string } } | undefined;
  Review: {
    projectId: string;
    projectName: string;
    hfov: number;
    lens?: string;
    segments: ReviewSegment[];
  };
  Processing: {
    projectId: string;
    projectName: string;
    imageUris?: string[];
    yaws?: number[];
    pitches?: number[];
    hfov?: number;
    lens?: string;
    retry?: boolean;
  };
  PrivacyConsent: {
    projectId: string;
    projectName: string;
    imageUris: string[];
    yaws: number[];
    pitches?: number[];
    hfov?: number;
    lens?: string;
  };
  Panorama: { projectId: string; projectName: string };
  World: { projectId: string; projectName: string; source?: 'original' | 'staged' };
};

const Stack = createNativeStackNavigator<RootStackParamList>();

export default function Navigation() {
  return (
    <NavigationContainer>
      <Stack.Navigator
        screenOptions={{
          headerShown: false,
          contentStyle: { backgroundColor: '#1a1a2e' },
          animation: 'slide_from_right',
        }}
      >
        <Stack.Screen name="Home" component={HomeScreen} />
        <Stack.Screen name="Capture" component={CaptureScreen} />
        <Stack.Screen name="CaptureCoach" component={CaptureCoachScreen} options={{ presentation: 'modal' }} />
        <Stack.Screen name="Review" component={ReviewScreen} />
        <Stack.Screen name="Processing" component={ProcessingScreen} />
        <Stack.Screen name="PrivacyConsent" component={PrivacyConsentScreen} />
        <Stack.Screen name="Panorama" component={PanoramaScreen} />
        <Stack.Screen name="World" component={WorldScreen} />
      </Stack.Navigator>
    </NavigationContainer>
  );
}
