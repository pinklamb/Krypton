import { module1State } from "../../module1/lib/module1State.js";
import { module2State } from "../../module2/lib/module2State.js";
import { module3State } from "../../module3/lib/module3State.js";
export const module0State = { value: 1 };
export function read() {
  return module1State.value + module2State.value + module3State.value;
}
