import { module0State } from "../../module0/lib/module0State.js";
import { module2State } from "../../module2/lib/module2State.js";
import { module3State } from "../../module3/lib/module3State.js";
export const module1State = { value: 1 };
export function read() {
  return module0State.value + module2State.value + module3State.value;
}
