import { module0State } from "../../module0/lib/module0State.js";
import { module1State } from "../../module1/lib/module1State.js";
import { module3State } from "../../module3/lib/module3State.js";
export const module2State = { value: 1 };
export function read() {
  return module0State.value + module1State.value + module3State.value;
}
