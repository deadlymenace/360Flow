import { File as ExpoFile, Paths } from 'expo-file-system';

const CONSENT_FILE = 'ai-disclosure-accepted.txt';

function consentFile(): ExpoFile {
  return new ExpoFile(Paths.document, CONSENT_FILE);
}

/** True after the person accepts the one-time third-party AI disclosure. */
export function hasAiDisclosureConsent(): boolean {
  return consentFile().exists;
}

export function acceptAiDisclosure(): void {
  const file = consentFile();
  if (!file.exists) {
    file.create();
  }
  file.write('accepted');
}

const COACH_FILE = 'capture-coach-seen.txt';

function coachFile(): ExpoFile {
  return new ExpoFile(Paths.document, COACH_FILE);
}

/** True after the first-run capture coach has been shown once. */
export function hasSeenCaptureCoach(): boolean {
  try {
    return coachFile().exists;
  } catch {
    return true;
  }
}

export function markCaptureCoachSeen(): void {
  try {
    const file = coachFile();
    if (!file.exists) file.create();
    file.write('seen');
  } catch {
    // A missing flag only means the coach shows again.
  }
}
