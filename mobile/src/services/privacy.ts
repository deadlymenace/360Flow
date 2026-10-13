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
