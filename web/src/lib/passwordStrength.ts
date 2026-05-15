const WEAK_PASSWORDS = new Set([
  "12345678",
  "123456789",
  "1234567890",
  "password",
  "password1",
  "password12",
  "password123",
  "qwerty12",
  "qwerty123",
  "qwertyuiop",
  "letmein",
  "letmein123",
  "iloveyou",
  "monkey123",
  "welcome1",
  "welcome123",
  "admin1234",
  "admin12345",
  "administrator",
  "1q2w3e4r",
  "1qaz2wsx",
  "zaq12wsx",
  "11111111",
  "00000000",
  "abcdefgh",
  "abc12345",
  "stockmgr",
  "stockmanager",
]);

export const PASSWORD_TOO_SHORT_MESSAGE = "Password must be at least 8 characters.";
export const PASSWORD_BLOCKLIST_MESSAGE = "Password is too common. Choose a more unique password.";
export const PASSWORD_REPETITIVE_MESSAGE = "Use a less repetitive password.";

export function getPasswordStrengthError(password: string): string | null {
  if (password.length < 8) {
    return PASSWORD_TOO_SHORT_MESSAGE;
  }
  if (WEAK_PASSWORDS.has(password.toLowerCase())) {
    return PASSWORD_BLOCKLIST_MESSAGE;
  }
  if (new Set(password).size < 4) {
    return PASSWORD_REPETITIVE_MESSAGE;
  }
  return null;
}
