import React from 'react';
import { NavigationContainer } from '@react-navigation/native';
import { createNativeStackNavigator } from '@react-navigation/native-stack';

import HomeScreen from './screens/HomeScreen';
import CaptureScreen from './screens/CaptureScreen';
import ReviewScreen from './screens/ReviewScreen';
import ProcessingScreen from './screens/ProcessingScreen';
import PanoramaScreen from './screens/PanoramaScreen';

export type RootStackParamList = {
  Home: undefined;
  Capture: { projectId: string; projectName: string };
  Review: {
    projectId: string;
    projectName: string;
    segments: { id: number; imageUri: string | null }[];
  };
  Processing: {
    projectId: string;
    projectName: string;
    imageUris?: string[];
  };
  Panorama: { projectId: string; projectName: string };
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
      </Stack.Navigator>
    </NavigationContainer>
  );
}
