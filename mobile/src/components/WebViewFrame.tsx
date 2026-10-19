import React, { useState } from 'react';
import { View, Text, StyleSheet, ActivityIndicator, TouchableOpacity, StyleProp, ViewStyle } from 'react-native';
import { WebView, WebViewProps } from 'react-native-webview';

type Props = Omit<WebViewProps, 'source'> & {
  uri: string;
  style?: StyleProp<ViewStyle>;
  /** Shown on the error card. */
  label?: string;
};

/** WebView with a loading overlay and an error card with Reload (bumps the key). */
export default function WebViewFrame({ uri, style, label = 'this view', ...rest }: Props) {
  const [reloads, setReloads] = useState(0);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);

  const reload = () => {
    setFailed(false);
    setLoading(true);
    setReloads((n) => n + 1);
  };

  return (
    <View style={styles.wrap}>
      {!failed ? (
        <WebView
          key={`${uri}#${reloads}`}
          source={{ uri }}
          style={style}
          onLoadStart={() => setLoading(true)}
          onLoadEnd={() => setLoading(false)}
          onError={() => {
            setFailed(true);
            setLoading(false);
          }}
          onHttpError={(event) => {
            if (event.nativeEvent.statusCode >= 400) {
              setFailed(true);
              setLoading(false);
            }
          }}
          {...rest}
        />
      ) : null}
      {loading && !failed ? (
        <View style={styles.overlay} pointerEvents="none" accessibilityLabel={`Loading ${label}`}>
          <ActivityIndicator size="large" color="#8F88FF" />
        </View>
      ) : null}
      {failed ? (
        <View style={styles.errorCard} accessibilityRole="alert">
          <Text style={styles.errorTitle}>Couldn't load {label}</Text>
          <Text style={styles.errorBody}>Check your connection, then reload.</Text>
          <TouchableOpacity style={styles.reload} onPress={reload} accessibilityRole="button">
            <Text style={styles.reloadText}>Reload</Text>
          </TouchableOpacity>
        </View>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: { flex: 1, backgroundColor: '#000' },
  overlay: { ...StyleSheet.absoluteFill, alignItems: 'center', justifyContent: 'center', backgroundColor: '#000' },
  errorCard: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: 24, gap: 8, backgroundColor: '#1a1a2e' },
  errorTitle: { color: '#fff', fontSize: 18, fontWeight: '700', textAlign: 'center' },
  errorBody: { color: '#b8b5d1', fontSize: 15, textAlign: 'center' },
  reload: {
    marginTop: 12,
    backgroundColor: '#6C63FF',
    borderRadius: 12,
    minHeight: 48,
    paddingHorizontal: 28,
    justifyContent: 'center',
  },
  reloadText: { color: '#fff', fontWeight: '700', fontSize: 16 },
});
