import { module0State } from "../../module0/lib/module0State.js";
import { module1State } from "../../module1/lib/module1State.js";
import { module2State } from "../../module2/lib/module2State.js";
import { module4State } from "../../module4/lib/module4State.js";
import { module5State } from "../../module5/lib/module5State.js";
export const module3State = { value: 1 };
export function read() {
  return module0State.value + module1State.value + module2State.value + module4State.value + module5State.value;
}
