// React hook for reading personality settings and reacting to changes. Uses
// plain useState + an event listener (not useSyncExternalStore) so the cached
// object reference stays stable across re-renders.
import { useCallback, useEffect, useState } from "react";
import { getPersonality, setPersonality as persist } from "./store";
import type { PersonalitySettings } from "./types";

export function usePersonality() {
  const [settings, setSettings] = useState<PersonalitySettings>(() =>
    getPersonality(),
  );

  useEffect(() => {
    const onPersonality = () => setSettings(getPersonality());
    window.addEventListener("infinity:personality", onPersonality);
    return () =>
      window.removeEventListener("infinity:personality", onPersonality);
  }, []);

  const setPersonality = useCallback(
    (patch: Partial<PersonalitySettings>) => persist(patch),
    [],
  );

  return { settings, setPersonality };
}