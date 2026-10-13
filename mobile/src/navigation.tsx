import React from 'react';
import { NavigationContainer } from '@react-navigation/native';
import { createNativeStackNavigator } from '@react-navigation/native-stack';

import HomeScreen from './screens/HomeScreen';
import CaptureScreen from './screens/CaptureScreen';
import ReviewScreen from './screens/ReviewScreen';
import ProcessingScreen from './screens/ProcessingScreen';
import PanoramaScreen from './screens/PanoramaScreen';
import WorldScreen from './screens/WorldScreen';

export type ViewerMode = 'original' | 'staged' | 'compare';

export type ReviewSegment = {
  id: number;
  imageUri: string | null;
  actualYaw: number | null;
};

export type RootStackParamList = {
  Home: undefined;
  Capture: { projectId: string; projectName: string };
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
    hfov?: number;
    lens?: string;
    retry?: boolean;
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
        <Stack.Screen name="Review" component={ReviewScreen} />
        <Stack.Screen name="Processing" component={ProcessingScreen} />
        <Stack.Screen name="Panorama" component={PanoramaScreen} />
        <Stack.Screen name="World" component={WorldScreen} />
      </Stack.Navigator>
    </NavigationContainer>
  );
}
