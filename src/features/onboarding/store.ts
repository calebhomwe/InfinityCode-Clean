// Onboarding completion flag. A per-device localStorage marker so the first-run
// welcome only appears once. Safe defaults: if storage is unavailable we treat
// the user as onboarded so nobody gets trapped behind the wizard.

const KEY = "infinity-onboarded";

export function isOnboarded(): boolean {
  try {
    return localStorage.getItem(KEY) === "1";
  } catch {
    return true;
  }
}

export function markOnboarded(): void {
  try {
    localStorage.setItem(KEY, "1");
  } catch {
    /* ignore — treating storage as unavailable keeps the app usable */
  }
}