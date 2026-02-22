import * as Haptics from 'expo-haptics';

export const HapticFeedback = {
  capture: () => Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium),
  success: () => Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success),
  error: () => Haptics.notificationAsync(Haptics.NotificationFeedbackType.Error),
  light: () => Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light),
};
