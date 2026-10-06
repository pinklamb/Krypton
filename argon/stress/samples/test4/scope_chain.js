function outer(a, b, c, d) {
  if (a) {
    class Inner {
      method() {
        if (b) {
          const arrow = () => {
            if (c) {
              const lambda = () => {
                if (d) {
                  return 1;
                }
                return 0;
              };
              return lambda();
            }
            return 2;
          };
          return arrow();
        }
        return 3;
      }
    }
    return new Inner().method();
  }
  return 4;
}
