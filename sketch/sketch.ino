// GlassMaster LT button-press actuator — MCU (real-time) side of the Uno Q.
//
// Deliberately does one thing: on request from the Linux/Python side, press
// one of the GM LT controller's three physical buttons (ENTER, UP, DOWN)
// with a servo, hold it for the requested duration, then release. All
// sequencing/decision logic lives in python/kiln/sequencer.py — this sketch
// has no idea what a "profile" or a "GlassFire program" is, and that's by
// design (see docs/architecture.md).
//
// Bridge API used here (arduino-libraries/Arduino_RouterBridge, confirmed
// against public Uno Q examples/docs — verify against your installed
// library version):
//   Bridge.begin()
//   Bridge.provide_safe(name, fn)   // fn runs on the dedicated hardware
//                                    // update thread; use for anything
//                                    // touching servos/pins.
//
// Python calls these by name via arduino.app_utils Bridge.call(...).result()
// — see python/kiln/bridge.py.

#include <Arduino_RouterBridge.h>
#include <Servo.h>

// ---- Keys ------------------------------------------------------------
// Must match kiln.protocol.Key values (ENTER=0, UP=1, DOWN=2) in
// python/kiln/protocol.py — keep the two in sync.
enum Key : uint8_t {
  KEY_ENTER = 0,
  KEY_UP = 1,
  KEY_DOWN = 2,
  KEY_COUNT = 3,
};

// ---- Pin / angle configuration ----------------------------------------
// TODO(hardware): confirm real pin numbers once the actuators are wired,
// and tune REST/PRESS angles per docs/hardware.md's calibration procedure.
// These can also be nudged at runtime via calibrate_servo() below, so the
// physical build doesn't require a reflash to dial in.
const uint8_t SERVO_PIN[KEY_COUNT] = {9, 10, 11};

// Angle (degrees) with the actuator clear of the button.
int rest_angle[KEY_COUNT] = {90, 90, 90};
// Angle (degrees) that fully depresses the button without stalling the
// servo against the panel.
int press_angle[KEY_COUNT] = {60, 60, 60};

Servo servos[KEY_COUNT];

static bool key_is_valid(int which) {
  return which >= 0 && which < KEY_COUNT;
}

// ---- Bridge-exposed functions ------------------------------------------

// Press `which` key (see Key enum) and hold it for hold_ms before
// releasing. Returns true on success, false on an invalid key index.
// Blocking for the duration of the press+release — that's intentional,
// it's a physical action with a real duration; the Python side calls this
// as a blocking RPC and treats the return as "the mechanical press is
// done" (NOT as confirmation the controller reacted the way we wanted —
// that's what vision.py is for).
bool press_button(int which, int hold_ms) {
  if (!key_is_valid(which)) {
    return false;
  }
  if (hold_ms < 0) {
    hold_ms = 0;
  }

  servos[which].write(press_angle[which]);
  delay(hold_ms > 0 ? hold_ms : 150);  // default: a short, deliberate tap
  servos[which].write(rest_angle[which]);
  return true;
}

// Runtime calibration hook so servo angles can be tuned from the Python
// side (e.g. a Settings screen) without reflashing the sketch. Values are
// NOT persisted across power cycles here — python/config/servo_calibration.yaml
// is the durable source of truth and should re-push these on every startup.
bool calibrate_servo(int which, int rest_deg, int press_deg) {
  if (!key_is_valid(which)) {
    return false;
  }
  rest_angle[which] = rest_deg;
  press_angle[which] = press_deg;
  servos[which].write(rest_angle[which]);
  return true;
}

// Cheap liveness check for the Python side to call at startup before
// trusting the bridge is up.
bool ping() {
  return true;
}

void setup() {
  for (uint8_t i = 0; i < KEY_COUNT; i++) {
    servos[i].attach(SERVO_PIN[i]);
    servos[i].write(rest_angle[i]);
  }

  Bridge.begin();
  Bridge.provide_safe("press_button", press_button);
  Bridge.provide_safe("calibrate_servo", calibrate_servo);
  Bridge.provide_safe("ping", ping);
}

void loop() {
  // All work happens in the Bridge-dispatched functions above; nothing to
  // poll here.
}
