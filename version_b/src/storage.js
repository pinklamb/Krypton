Racer.storage = {
  load() {
    try {
      const saved = JSON.parse(localStorage.getItem(Racer.config.storageKey));
      return Array.isArray(saved) ? saved : [];
    } catch (err) {
      return [];
    }
  },

  save(list) {
    try {
      localStorage.setItem(Racer.config.storageKey, JSON.stringify(list));
    } catch (err) {
      // storage unavailable: achievements still live for this session
    }
  },
};
