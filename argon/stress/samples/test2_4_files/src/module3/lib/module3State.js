import { module0State } from "../../module0/lib/module0State.js";
import { module1State } from "../../module1/lib/module1State.js";
import { module2State } from "../../module2/lib/module2State.js";
export const module3State = { value: 1 };
export function read() {
  return module0State.value + module1State.value + module2State.value;
}
