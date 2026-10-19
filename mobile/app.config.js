// Fails `eas build` and `eas update` for release profiles when the API URL is
// missing, instead of shipping a bundle that throws at launch.
// app.json stays the base config; this only adds the check.

const RELEASE_PROFILES = new Set(['preview', 'production']);

function isReleaseBuild() {
  const profile = process.env.EAS_BUILD_PROFILE;
  if (profile) return RELEASE_PROFILES.has(profile);
  // `eas update` and `expo export` run without a build profile.
  const argv = process.argv.join(' ');
  return /\b(update|export)\b/.test(argv) || process.env.EXPO_REQUIRE_API_URL === '1';
}

module.exports = ({ config }) => {
  if (isReleaseBuild()) {
    const url = (process.env.EXPO_PUBLIC_API_URL || '').trim();
    if (!url) {
      throw new Error(
        'EXPO_PUBLIC_API_URL must be set for release builds and updates (see mobile/.env.example).'
      );
    }
    if (!/^https:\/\//i.test(url)) {
      throw new Error('EXPO_PUBLIC_API_URL must use https for release builds and updates.');
    }
  }
  return config;
};
