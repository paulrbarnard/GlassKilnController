// GlassMaster LT button-press actuator — MCU (real-time) side of the Uno Q.
//
// Deliberately does one thing: on request from the Linux/Python side, press
// one of the GM LT controller's three physical buttons (ENTER, UP, DOWN)
// with a 12V push-pull solenoid, hold it for the requested duration, then
// release. All sequencing/decision logic lives in python/kiln/sequencer.py
// — this sketch has no idea what a "profile" or a "GlassFire program" is,
// and that's by design (see docs/architecture.md).
//
// Bridge API used here (arduino-libraries/Arduino_RouterBridge) is
// CONFIRMED by compiling and running on real hardware (an Uno Q named
// "Steffi"), not just documentation:
//   Bridge.begin()
//   Bridge.provide_safe(name, fn)   // fn runs on the dedicated hardware
//                                    // update thread; use for anything
//                                    // touching pins.
//
// Python calls these by name via arduino.app_utils Bridge.call(...).result()
// — see python/kiln/bridge.py.
//
// Drive: each solenoid hangs off one channel of a MonkMakes Mosfetti
// (3.3V-logic MOSFET board with flyback diodes, separate 12V supply; see
// docs/hardware.md). A press is a short full-power kick to pull the plunger
// in, then a reduced PWM duty to hold it there without cooking the coil.
// This board's PWM pins (D2-D13, D20/D21) are hardware-fixed at 500Hz,
// which is fine for a solenoid hold.
#include <Arduino_RouterBridge.h>

// ---- Keys ------------------------------------------------------------
// Must match kiln.protocol.Key values (ENTER=0, UP=1, DOWN=2) in
// python/kiln/protocol.py — keep the two in sync.
enum Key : uint8_t {
  KEY_ENTER = 0,
  KEY_UP = 1,
  KEY_DOWN = 2,
  KEY_COUNT = 3,
};

// ---- Pin / drive configuration ------------------------------------------
// Mosfetti gate inputs. Tune kick/hold per docs/hardware.md; they can also
// be set at runtime via configure_solenoid() below, so dialling in the
// physical build doesn't need a reflash.
const uint8_t SOLENOID_PIN[KEY_COUNT] = {9, 10, 11};
// Mosfetti channel 4 is wired to D12 but unused; held LOW so it stays off.
const uint8_t SPARE_PIN = 12;

// Full-power pull-in time (ms) at the start of every press.
int kick_ms[KEY_COUNT] = {100, 100, 100};
// PWM duty (0-255) used to hold the plunger in after the kick.
int hold_duty[KEY_COUNT] = {100, 100, 100};
// Long presses (the 5 s "hold" actions) drop to a lower duty once the
// button has been held for settle_ms: a membrane dome needs less force to
// stay closed than to close, and 5 s at the full hold duty heats the coil.
int settle_ms[KEY_COUNT] = {200, 200, 200};
int long_hold_duty[KEY_COUNT] = {255, 255, 255};  // 255 = no reduction

// Hard cap on a single press, so a bug can't leave a coil energised. The
// longest presses are held UP/DOWN to scroll a value: the GM LT waits ~2 s
// before repeating, then speeds up the longer the button is held.
const int MAX_PRESS_MS = 20000;
const int DEFAULT_PRESS_MS = 200;  // a short, deliberate tap

// ---- Heat budget -------------------------------------------------------
// The solenoids are rated 5% duty in 60 s at 12 V: about 3 s of full power
// a minute (one was burnt out by repeated long scroll holds). Each coil's
// heat is tracked in "full-power seconds": a press adds its cost (power
// goes with the square of the PWM duty) and it drains at the rated 0.05/s.
// The Python side waits for coils to cool before pressing (kiln/bridge.py,
// same model, ceiling 3.0); this is the safety net behind it, so a press
// that would take a coil past HEAT_LIMIT is refused even if Python is wrong.
const float HEAT_DRAIN_PER_S = 0.05f;
const float HEAT_LIMIT = 4.5f;
float heat[KEY_COUNT] = {0, 0, 0};
unsigned long heat_ms[KEY_COUNT] = {0, 0, 0};

static float duty_power(int duty) {
  float d = duty / 255.0f;
  return d * d;
}

static float press_cost(int which, int hold_ms) {
  int kick = min(hold_ms, kick_ms[which]);
  float cost = kick / 1000.0f;
  if (hold_ms > kick) {
    int settle = max(settle_ms[which], kick_ms[which]);
    bool reduce = hold_ms > settle && long_hold_duty[which] < hold_duty[which];
    int at_hold = (reduce ? settle : hold_ms) - kick;
    cost += at_hold / 1000.0f * duty_power(hold_duty[which]);
    if (reduce) {
      cost += (hold_ms - settle) / 1000.0f * duty_power(long_hold_duty[which]);
    }
  }
  return cost;
}

static void drain_heat(int which) {
  unsigned long now = millis();
  heat[which] -= (now - heat_ms[which]) / 1000.0f * HEAT_DRAIN_PER_S;
  if (heat[which] < 0) heat[which] = 0;
  heat_ms[which] = now;
}

static bool key_is_valid(int which) {
  return which >= 0 && which < KEY_COUNT;
}

static void all_off() {
  for (uint8_t i = 0; i < KEY_COUNT; i++) {
    analogWrite(SOLENOID_PIN[i], 0);
  }
}

// ---- Bridge-exposed functions ------------------------------------------

// Press `which` key (see Key enum) and hold it for hold_ms before
// releasing. Returns true on success, false on an invalid key index or a
// hold longer than MAX_PRESS_MS. Blocking for the duration of the press —
// that's intentional, it's a physical action with a real duration; the
// Python side calls this as a blocking RPC and treats the return as "the
// mechanical press is done" (NOT as confirmation the controller reacted
// the way we wanted — that's what vision.py is for).
//
// Only one solenoid is ever energised: everything is switched off before
// a press starts and again when it ends.
bool press_button(int which, int hold_ms) {
  if (!key_is_valid(which) || hold_ms > MAX_PRESS_MS) {
    return false;
  }
  if (hold_ms <= 0) {
    hold_ms = DEFAULT_PRESS_MS;
  }
  drain_heat(which);
  float cost = press_cost(which, hold_ms);
  if (heat[which] + cost > HEAT_LIMIT) {
    return false;  // would overheat this coil: refuse (see heat budget)
  }
  heat[which] += cost;

  const uint8_t pin = SOLENOID_PIN[which];
  all_off();
  analogWrite(pin, 255);
  if (hold_ms <= kick_ms[which]) {
    delay(hold_ms);
  } else if (hold_ms <= settle_ms[which] || long_hold_duty[which] >= hold_duty[which]) {
    delay(kick_ms[which]);
    analogWrite(pin, hold_duty[which]);
    delay(hold_ms - kick_ms[which]);
  } else {
    delay(kick_ms[which]);
    analogWrite(pin, hold_duty[which]);
    delay(settle_ms[which] - kick_ms[which]);
    analogWrite(pin, long_hold_duty[which]);
    delay(hold_ms - settle_ms[which]);
  }
  all_off();
  return true;
}

// Runtime tuning hook so kick/hold can be adjusted from the Python side
// without reflashing the sketch. Values are NOT persisted across power
// cycles here — python/config/solenoid_calibration.yaml is the durable
// source of truth and is re-pushed on every startup (main.py).
bool configure_solenoid(int which, int kick, int duty) {
  if (!key_is_valid(which) || kick < 0 || kick > 500 || duty < 0 || duty > 255) {
    return false;
  }
  kick_ms[which] = kick;
  hold_duty[which] = duty;
  return true;
}

// Long-press tuning: after ``settle`` ms of a press, drop to ``duty``.
// Like configure_solenoid, re-pushed from solenoid_calibration.yaml at
// startup.
bool configure_long_hold(int which, int settle, int duty) {
  if (!key_is_valid(which) || settle < kick_ms[which] || settle > 2000 || duty < 0 || duty > 255) {
    return false;
  }
  settle_ms[which] = settle;
  long_hold_duty[which] = duty;
  return true;
}

// Cheap liveness check for the Python side to call at startup before
// trusting the bridge is up.
bool ping() {
  return true;
}

void setup() {
  // Drive every gate LOW before anything else, so no solenoid fires while
  // the Bridge comes up.
  for (uint8_t i = 0; i < KEY_COUNT; i++) {
    pinMode(SOLENOID_PIN[i], OUTPUT);
    digitalWrite(SOLENOID_PIN[i], LOW);
  }
  pinMode(SPARE_PIN, OUTPUT);
  digitalWrite(SPARE_PIN, LOW);
  all_off();

  Bridge.begin();
  Bridge.provide_safe("press_button", press_button);
  Bridge.provide_safe("configure_solenoid", configure_solenoid);
  Bridge.provide_safe("configure_long_hold", configure_long_hold);
  Bridge.provide_safe("ping", ping);
}

void loop() {
  // All work happens in the Bridge-dispatched functions above; nothing to
  // poll here.
}
