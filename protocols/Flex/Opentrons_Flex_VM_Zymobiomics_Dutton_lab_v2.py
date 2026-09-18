"""
ZymoBIOMICS 96 DNA Kit (D4303 / D4307 / D4309) on the Opentrons Flex
with the Vacuum Module.

Rewrite of the Opentrons draft protocol
(Opentrons_Flex_VM_Zymobiomics_Dutton_lab.py) for the Dutton lab.

----------------------------------------------------------------------
OPERATOR PREPARES OFF-DECK
  1. Bead-beat and centrifuge samples per kit manual steps 1-4.
  2. Transfer up to 400 uL of clarified supernatant into each well of the
     2 mL deep-well sample plate (deck A2).
  3. Pour reagents into the troughs per the REAGENT SETUP report that this
     protocol prints at the start of the run.  Pour volumes are set by the
     *_FILL constants below.
  4. Prepare the Silicon-A-HRC Plate per the kit's "Before Starting"
     section.  Kit manual step 14 (HRC inhibitor removal) is run by
     centrifuge AFTER this protocol finishes.  It is deliberately NOT
     automated here -- do not skip the chemistry, only the automation.

ROBOT PERFORMS  (kit manual steps 6-13)
  1. Add 1200 uL DNA Binding Buffer to each well (2 x 600 uL), then mix.
  2. Transfer 2 x 800 uL onto the Zymo-Spin I-96-Z Plate, vacuum after each.
  3. 400 uL DNA Wash Buffer 1, vacuum.
  4. 700 uL DNA Wash Buffer 2, vacuum.
  5. 200 uL DNA Wash Buffer 2, vacuum.
  6. Extended vacuum to dry the membranes.
  7. Gripper repositions the filter plate over the Elution Plate.
  8. Add elution water, incubate, vacuum into the Elution Plate.

----------------------------------------------------------------------
PLACEHOLDERS -- MUST BE RESOLVED BEFORE A REAL RUN

  * FILTER_PLATE_LOADNAME and ELUTION_PLATE_LOADNAME are stand-in labware.
    Custom definitions for the Zymo-Spin I-96-Z Plate and the kit Elution
    Plate are required.  EVERY z-height in this file is provisional until
    those exist, and ELUTION_DISPENSE_Z most of all.

  * All FR_* flow rates are STARTING POINTS, not validated values.
    Before tuning, print the pipette defaults in simulation:
        print(pip.flow_rate.aspirate, pip.flow_rate.dispense,
              pip.flow_rate.blow_out)
    Then validate gravimetrically: dispense into a tared plate, weigh,
    compare actual vs nominal.

  * Flow rates and volumes on a multi-channel pipette are PER CHANNEL.
    aspirate(600) on the 96-channel moves 600 uL per nozzle = 57.6 mL total.

----------------------------------------------------------------------
DESIGN NOTES

  * Full 96-nozzle (ALL) pickup throughout.  Partial pickup was evaluated
    and rejected: a 12-well reservoir does not improve endpoint liquid
    depth (the kit's ~8% buffer overage sets that, not the vessel), and
    COLUMN configuration introduces head-to-collar collision risk.

  * Because ALL pickup always dispenses to 96 wells, reagent consumption
    is fixed regardless of how many wells hold sample.  Reducing sample
    count does not reduce buffer use.

  * Trough endpoint depth is computed and reported at run start.  Below
    MIN_RESIDUAL_DEPTH_MM the residual film over the trough ribs becomes
    discontinuous and the 96-tip draw can take air unevenly -- a silent
    failure.  Pour more, do not run shallower.

  * Plate layout should be randomised with respect to individual and
    timepoint.  Any position-structured technical effect (elution soak,
    collar seal, vacuum uniformity, edge effects) otherwise loads onto
    the study's primary axis.
"""

from typing import cast

from opentrons import protocol_api
from opentrons.protocol_api import (
    ParameterContext,
    ProtocolContext,
    VacuumModuleContext,
)

metadata = {
    "protocolName": "ZymoBIOMICS 96 DNA Kit - Flex + Vacuum Module (Dutton lab v2)",
    "author": "Tavis Goldwire, after Opentrons Inc.",
    "description": (
        "96-well silica-plate DNA extraction with vacuum manifold. "
        "96-channel pipette, full tip pickup. Ends at elution; HRC "
        "inhibitor removal is performed off-deck."
    ),
    "source": "Dutton lab",
}

requirements = {
    "robotType": "Flex",
    "apiLevel": "2.30",
}

# =====================================================================
# LABWARE
# =====================================================================
# !! PLACEHOLDERS !!  Replace with custom definitions once created.
FILTER_PLATE_LOADNAME = "thermoscientificnunc_96_wellplate_1000ul_filter"
ELUTION_PLATE_LOADNAME = "eppendorf_96_wellplate_150ul"

SAMPLE_PLATE_LOADNAME = "nest_96_wellplate_2ml_deep"
RESERVOIR_LOADNAME = "nest_1_reservoir_195ml"
TIPRACK_LOADNAME = "opentrons_flex_96_tiprack_1000ul"

# nest_1_reservoir_195ml, well A1 footprint, from the Opentrons labware
# definition.  1 uL over 1 mm^2 = 1 mm of depth, so residual_uL / area
# gives residual depth directly in mm.
TROUGH_AREA_MM2 = 106.8 * 71.2   # = 7604.16

# =====================================================================
# PROTOCOL VOLUMES  (kit manual steps 5-13, per well, uL)
# =====================================================================
LYSATE_VOLUME = 400              # step 5, operator-loaded
BINDING_BUFFER_TOTAL = 1200      # step 6
BINDING_BUFFER_ALIQUOT = 600     # delivered as 2 x 600 (1000 uL tips)
LOAD_VOLUME = 800                # steps 7 & 8: 400 + 1200 = 1600 = 2 x 800
LOAD_REPEATS = 2
WASH1_VOLUME = 400               # step 10
WASH2_VOLUME_A = 700             # step 11
WASH2_VOLUME_B = 200             # step 12
# Elution volume (step 13) is a runtime parameter -- see add_parameters().

# =====================================================================
# REAGENT POUR VOLUMES (uL)
# =====================================================================
# Do NOT split the kit bottles into two 96-prep portions.  A half-kit
# portion of Binding Buffer (125 mL) leaves only 9.8 mL residual = 1.3 mm
# of depth at the final draw.  Pouring 150 mL leaves 4.6 mm.  The cost is
# ~1.5 runs per kit instead of 2.
#
# Upper bound is set by tip displacement, not the trough's 195 mL nominal
# capacity: 96 submerged tips displace roughly 40-55 mL of apparent level.
# Confirm empirically before raising these.
BINDING_BUFFER_FILL = 150_000    # of 250 mL bottle
WASH1_FILL = 80_000              # of 100 mL bottle
WASH2_FILL = 150_000             # of 200 mL bottle
WATER_FILL = 40_000              # bulk nuclease-free water, not kit water
                                 # (kit supplies only 10 mL for 2 x 96)

MIN_RESIDUAL_DEPTH_MM = 3.0      # warn below this

# =====================================================================
# LIQUID HANDLING TUNING
# =====================================================================
# Flow rates are PER CHANNEL, uL/s.  STARTING POINTS -- validate.
FR_BINDING_ASP = 80         # guanidinium salt solution, noticeably viscous
FR_BINDING_DISP = 150
FR_MIX_ASP = 150
FR_MIX_DISP = 150
FR_LYSATE_ASP = 100         # slow: avoid resuspending settled debris
FR_LYSATE_DISP = 120        # slow onto the membrane: avoid channeling
FR_WASH_ASP = 150
FR_WASH_DISP = 180
FR_WATER_ASP = 50
FR_WATER_DISP = 50
FR_BLOWOUT = 100
FR_ELUTION_BLOWOUT = 20     # low: a hard blow-out at the membrane splashes

# Viscous liquids keep entering the tip after the plunger stops.  Moving
# immediately leaves the tip short.  Applies to Binding Buffer and lysate.
DELAY_AFTER_VISCOUS_ASP_S = 2.0

# Air gaps prevent drips in transit.  NOTE: the air gap is part of the
# tip's contents -- every dispense below adds it back explicitly.  Getting
# this wrong short-dispenses silently.
AIR_GAP = 20
AIR_GAP_ELUTION = 10

# --- Z heights (mm above well bottom unless noted) -------------------
RESERVOIR_ASP_Z = 0.5       # not the lowest possible: bottom(z=0) is the
                            # DEFINED floor, and calibration error there
                            # presses the tip into plastic and occludes it
SAMPLE_ASP_Z_FIRST = 8.0    # 1600 uL in the deep block sits ~33 mm high
SAMPLE_ASP_Z_SECOND = 1.5   # second 800 uL draw takes the well to empty
MIX_ASP_Z = 2.0             # aspirate low, dispense high: this turns the
MIX_DISP_Z = 12.0           # well over.  Oscillating at one height (as
                            # the original draft did) does not mix.
DISPENSE_ABOVE_Z = -5.0     # relative to well TOP, non-contact dispense
ELUTION_DISPENSE_Z = 3.0    # !! PLACEHOLDER !! must be re-measured against
                            # the real Zymo-Spin I-96-Z geometry.  The kit
                            # manual says "directly to the column matrix";
                            # the original draft's z=10 leaves 50 uL
                            # clinging to the well wall.

# --- Mixing ----------------------------------------------------------
# With ALL pickup the whole plate mixes at once, so 6 cycles costs 1-2
# minutes for all 96 wells, not per well.  Time is not the constraint --
# do not trade mixing quality for it.
MIX_REPS = 6
MIX_VOLUME = 900            # ~56% turnover of the 1600 uL well per cycle


def add_parameters(parameters: ParameterContext):
    parameters.add_str(
        variable_name="collar",
        display_name="Vacuum Collar",
        description="Which Opentrons vacuum manifold collar is installed.",
        default="opentrons_vacuum_manifold_collar_tall",
        choices=[
            {
                "display_name": "Opentrons Short",
                "value": "opentrons_vacuum_manifold_collar_short",
            },
            {
                "display_name": "Opentrons Tall",
                "value": "opentrons_vacuum_manifold_collar_tall",
            },
        ],
    )

    # Kit manual specifies 20 uL for max concentration (Specifications
    # section allows up to 100 uL), but that 20 uL assumes CENTRIFUGATION.
    # This protocol uses vacuum instead, and small-volume recovery off the
    # membrane and frit may differ -- validate 20 vs 50 empirically before
    # trusting the low end. Opentrons caps parameter descriptions at 100
    # characters, so the rest of the rationale lives here, not below.
    parameters.add_float(
        variable_name="elution_volume",
        display_name="Elution Volume (uL)",
        description="Water added per well at elution (20-100 uL; see comment above).",
        default=50.0,
        minimum=20.0,
        maximum=100.0,
        unit="uL",
    )

    parameters.add_int(
        variable_name="vacuum_pressure",
        display_name="Vacuum Pressure (mbar)",
        description="Vacuum pressure for buffer pull-through. Negative.",
        default=-500,
        minimum=-800,
        maximum=-100,
        unit="mbar",
    )

    parameters.add_int(
        variable_name="vacuum_time",
        display_name="Vacuum Duration (s)",
        description="Duration of each buffer pull-through.",
        default=60,
        minimum=10,
        maximum=600,
        unit="s",
    )

    parameters.add_int(
        variable_name="dry_pressure",
        display_name="Membrane Dry Pressure (mbar)",
        description="Vacuum pressure for the membrane drying step.",
        default=-800,
        minimum=-800,
        maximum=-300,
        unit="mbar",
    )

    # Residual ethanol carryover is the most common cause of downstream
    # PCR failure -- this extended vacuum step dries the membranes to
    # prevent it. (Kept as a comment: Opentrons caps descriptions at 100
    # characters.)
    parameters.add_int(
        variable_name="dry_time",
        display_name="Membrane Dry Duration (s)",
        description="Extended vacuum to dry membranes (see comment above).",
        default=300,
        minimum=60,
        maximum=900,
        unit="s",
    )

    parameters.add_int(
        variable_name="elution_incubation",
        display_name="Elution Incubation (s)",
        description="Soak time on the membrane before the elution vacuum.",
        default=60,
        minimum=0,
        maximum=600,
        unit="s",
    )

    # For deck/motion checks only -- do not use with real samples. (Kept
    # as a comment: Opentrons caps descriptions at 100 characters.)
    parameters.add_bool(
        variable_name="dry_run",
        display_name="Dry Run",
        description="Skip incubations, shorten vacuum. Not for real samples.",
        default=False,
    )


def vacuum(ctx, vm_mod, pressure, time, vent=True, equalize_time=20):
    """
    Operate the vacuum manifold.

    pressure:      vacuum pressure in mbar (0 to -800)
    time:          duration of the vacuum in seconds
    vent:          open the vent after vacuuming
    equalize_time: seconds to let pressure equalise before the next move
    """
    vm_mod.close_vent()
    task = vm_mod.start_set_vacuum_pressure(
        pressure, time, vent_after=vent, equalize_timeout_s=equalize_time
    )
    ctx.wait_for_tasks([task])


def run(ctx: protocol_api.ProtocolContext):

    # -----------------------------------------------------------------
    # RUNTIME PARAMETERS
    # -----------------------------------------------------------------
    elution_volume = float(ctx.params.elution_volume)
    vac_pressure = int(ctx.params.vacuum_pressure)
    vac_time = int(ctx.params.vacuum_time)
    dry_pressure = int(ctx.params.dry_pressure)
    dry_time = int(ctx.params.dry_time)
    elution_incubation = int(ctx.params.elution_incubation)
    dry_run = bool(ctx.params.dry_run)

    if dry_run:
        vac_time = 5
        dry_time = 5
        elution_incubation = 0

    def hold(seconds):
        """Delay, unless this is a dry run."""
        if not dry_run and seconds > 0:
            ctx.delay(seconds=seconds)

    # -----------------------------------------------------------------
    # MODULES AND FIXTURES
    # -----------------------------------------------------------------
    waste_chute = ctx.load_waste_chute()

    vm_mod = cast(
        VacuumModuleContext,
        ctx.load_module(module_name="vacuumModuleV1", location="A3"),
    )
    manifold_collar = vm_mod.load_adapter(ctx.params.collar)

    # -----------------------------------------------------------------
    # LABWARE
    # -----------------------------------------------------------------
    sample_plate = ctx.load_labware(
        SAMPLE_PLATE_LOADNAME, "A2", "Sample plate - clarified lysate"
    )

    filter_plate = manifold_collar.load_labware(
        FILTER_PLATE_LOADNAME, "Zymo-Spin I-96-Z Plate"
    )

    # The elution plate rides on the tall spacer so the gripper can move
    # both together.  Moving the spacer moves its contents.
    tall_spacer = ctx.load_adapter("opentrons_vacuum_manifold_spacer_tall", "D2")
    elution_plate = tall_spacer.load_labware(ELUTION_PLATE_LOADNAME)

    binding_res = ctx.load_labware(RESERVOIR_LOADNAME, "B2", "DNA Binding Buffer")
    wash1_res = ctx.load_labware(RESERVOIR_LOADNAME, "B3", "DNA Wash Buffer 1")
    wash2_res = ctx.load_labware(RESERVOIR_LOADNAME, "C2", "DNA Wash Buffer 2")
    water_res = ctx.load_labware(RESERVOIR_LOADNAME, "C3", "Nuclease-free water")

    # Full-pickup tip racks must sit in the 96-tiprack adapter.
    # Four pickups: binding+load, wash 1, wash 2, elution. C1 now holds the
    # heater block, so only A1/B1/D1 carry tip racks on deck; the fourth
    # rack is staged in C4 and swapped onto D1 by the gripper before
    # elution -- see the REPLENISH block after STEP 5.
    tiprack_adapters = {
        slot: ctx.load_adapter("opentrons_flex_96_tiprack_adapter", slot)
        for slot in ("A1", "B1", "D1")
    }
    tip_racks = [
        tiprack_adapters[slot].load_labware(TIPRACK_LOADNAME)
        for slot in ("A1", "B1", "D1")
    ]
    tips_d1 = tip_racks[-1]

    # Staged spare rack for the gripper reload below. C4 (not A4, which
    # vm_mod.manifold_dock already uses; not D4, which needs a deck plate
    # adapter to coexist with the waste chute in D3) must be enabled in
    # the robot's deck configuration (touchscreen or Opentrons App).
    spare_tips = ctx.load_labware(
        TIPRACK_LOADNAME, "C4",
        "Spare tip rack, staged for gripper reload before elution",
    )

    # -----------------------------------------------------------------
    # PIPETTE
    # -----------------------------------------------------------------
    # flex_96channel_1000, not _200: the 600 uL binding buffer aliquot
    # exceeds the 200 uL head's range.  Default nozzle layout is ALL.
    pip = ctx.load_instrument("flex_96channel_1000", "left", tip_racks=tip_racks)

    # -----------------------------------------------------------------
    # LIQUIDS
    # -----------------------------------------------------------------
    binding_buffer = ctx.define_liquid(
        name="DNA Binding Buffer", description="ZymoBIOMICS", display_color="#FF0000"
    )
    wash1_buffer = ctx.define_liquid(
        name="DNA Wash Buffer 1", description="ZymoBIOMICS", display_color="#FFFB00"
    )
    wash2_buffer = ctx.define_liquid(
        name="DNA Wash Buffer 2", description="ZymoBIOMICS", display_color="#00FF6E"
    )
    elution_water = ctx.define_liquid(
        name="Nuclease-free water", description="Elution", display_color="#0048FF"
    )
    lysate = ctx.define_liquid(
        name="Clarified lysate", description="Operator-loaded", display_color="#B266FF"
    )

    binding_res["A1"].load_liquid(liquid=binding_buffer, volume=BINDING_BUFFER_FILL)
    wash1_res["A1"].load_liquid(liquid=wash1_buffer, volume=WASH1_FILL)
    wash2_res["A1"].load_liquid(liquid=wash2_buffer, volume=WASH2_FILL)
    water_res["A1"].load_liquid(liquid=elution_water, volume=WATER_FILL)
    for well in sample_plate.wells():
        well.load_liquid(liquid=lysate, volume=LYSATE_VOLUME)

    # -----------------------------------------------------------------
    # REAGENT SETUP REPORT
    # -----------------------------------------------------------------
    # Consumption is fixed at 96 wells: ALL pickup dispenses to every well
    # whether or not it holds sample.
    reagents = [
        ("DNA Binding Buffer", BINDING_BUFFER_TOTAL * 96, BINDING_BUFFER_FILL, "B2"),
        ("DNA Wash Buffer 1", WASH1_VOLUME * 96, WASH1_FILL, "B3"),
        ("DNA Wash Buffer 2", (WASH2_VOLUME_A + WASH2_VOLUME_B) * 96, WASH2_FILL, "C2"),
        ("Nuclease-free water", elution_volume * 96, WATER_FILL, "C3"),
    ]

    ctx.comment("=" * 68)
    ctx.comment("REAGENT SETUP - pour these volumes before starting")
    ctx.comment("=" * 68)
    shallow = []
    for name, required, fill, slot in reagents:
        residual = fill - required
        residual_mm = residual / TROUGH_AREA_MM2
        ctx.comment(
            f"  {slot}  {name}: pour {fill / 1000:.1f} mL "
            f"| uses {required / 1000:.1f} mL "
            f"| residual {residual / 1000:.1f} mL ({residual_mm:.1f} mm deep)"
        )
        if residual < 0:
            ctx.comment(f"       *** INSUFFICIENT: short by {-residual / 1000:.1f} mL")
            shallow.append(name)
        elif residual_mm < MIN_RESIDUAL_DEPTH_MM:
            ctx.comment(
                f"       *** SHALLOW: final draw ends at {residual_mm:.1f} mm. "
                f"Below {MIN_RESIDUAL_DEPTH_MM} mm the residual film over the "
                f"trough ribs breaks up and the 96-tip draw can take air."
            )
            shallow.append(name)
    ctx.comment("=" * 68)
    ctx.comment(f"  Elution volume: {elution_volume:.0f} uL per well")
    ctx.comment(f"  Vacuum: {vac_pressure} mbar for {vac_time} s per step")
    ctx.comment(f"  Membrane dry: {dry_pressure} mbar for {dry_time} s")
    if dry_run:
        ctx.comment("  *** DRY RUN - delays skipped, vacuum shortened ***")
    ctx.comment("=" * 68)

    if not dry_run:
        ctx.pause(
            "Confirm reagent volumes poured as listed above, sample plate "
            "loaded in A2, and the Silicon-A-HRC Plate prepared for the "
            "off-deck step after this run."
        )

    # =================================================================
    # STEP 1 - BINDING BUFFER + MIX          (kit manual step 6)
    # =================================================================
    ctx.comment(">> STEP 1: add DNA Binding Buffer and mix")
    pip.pick_up_tip()
    pip.flow_rate.blow_out = FR_BLOWOUT

    pip.flow_rate.aspirate = FR_BINDING_ASP
    pip.flow_rate.dispense = FR_BINDING_DISP

    for _ in range(BINDING_BUFFER_TOTAL // BINDING_BUFFER_ALIQUOT):
        pip.aspirate(BINDING_BUFFER_ALIQUOT, binding_res["A1"].bottom(z=RESERVOIR_ASP_Z))
        hold(DELAY_AFTER_VISCOUS_ASP_S)
        pip.air_gap(AIR_GAP)
        # Non-contact dispense from above keeps the tip clean, so the next
        # trip to the trough cannot carry lysate into the reagent stock.
        pip.dispense(
            BINDING_BUFFER_ALIQUOT + AIR_GAP,
            sample_plate["A1"].top(z=DISPENSE_ABOVE_Z),
        )
        pip.blow_out(sample_plate["A1"].top(z=DISPENSE_ABOVE_Z))

    pip.flow_rate.aspirate = FR_MIX_ASP
    pip.flow_rate.dispense = FR_MIX_DISP
    for _ in range(MIX_REPS):
        pip.aspirate(MIX_VOLUME, sample_plate["A1"].bottom(z=MIX_ASP_Z))
        pip.dispense(MIX_VOLUME, sample_plate["A1"].bottom(z=MIX_DISP_Z))
    pip.blow_out(sample_plate["A1"].top(z=DISPENSE_ABOVE_Z))

    # =================================================================
    # STEP 2 - LOAD FILTER PLATE             (kit manual steps 7-9)
    # =================================================================
    # 400 uL lysate + 1200 uL binding buffer = 1600 uL, moved as 2 x 800.
    # The original draft used 850, which pulls 100 uL of air on the second
    # pass.  Kit manual step 7 specifies 800; step 8 repeats step 7.
    ctx.comment(">> STEP 2: load lysate onto the Zymo-Spin I-96-Z Plate")
    pip.flow_rate.aspirate = FR_LYSATE_ASP
    pip.flow_rate.dispense = FR_LYSATE_DISP

    for asp_z in (SAMPLE_ASP_Z_FIRST, SAMPLE_ASP_Z_SECOND):
        pip.aspirate(LOAD_VOLUME, sample_plate["A1"].bottom(z=asp_z))
        hold(DELAY_AFTER_VISCOUS_ASP_S)
        pip.air_gap(AIR_GAP)
        pip.dispense(
            LOAD_VOLUME + AIR_GAP, filter_plate["A1"].top(z=DISPENSE_ABOVE_Z)
        )
        pip.blow_out(filter_plate["A1"].top(z=DISPENSE_ABOVE_Z))
        vacuum(ctx, vm_mod, vac_pressure, vac_time)

    pip.drop_tip(waste_chute)

    # =================================================================
    # STEP 3 - DNA WASH BUFFER 1             (kit manual step 10)
    # =================================================================
    ctx.comment(">> STEP 3: DNA Wash Buffer 1")
    pip.pick_up_tip()
    pip.flow_rate.aspirate = FR_WASH_ASP
    pip.flow_rate.dispense = FR_WASH_DISP

    pip.aspirate(WASH1_VOLUME, wash1_res["A1"].bottom(z=RESERVOIR_ASP_Z))
    pip.air_gap(AIR_GAP)
    pip.dispense(WASH1_VOLUME + AIR_GAP, filter_plate["A1"].top(z=DISPENSE_ABOVE_Z))
    pip.blow_out(filter_plate["A1"].top(z=DISPENSE_ABOVE_Z))
    pip.drop_tip(waste_chute)

    vacuum(ctx, vm_mod, vac_pressure, vac_time)

    # =================================================================
    # STEP 4 - DNA WASH BUFFER 2 + DRY       (kit manual steps 11-12)
    # =================================================================
    # One tip pickup serves both Wash 2 additions: same reagent, dispensed
    # without contact, so there is nothing to cross-contaminate.
    ctx.comment(">> STEP 4: DNA Wash Buffer 2 (700 uL then 200 uL)")
    pip.pick_up_tip()
    pip.flow_rate.aspirate = FR_WASH_ASP
    pip.flow_rate.dispense = FR_WASH_DISP

    for wash2_volume in (WASH2_VOLUME_A, WASH2_VOLUME_B):
        pip.aspirate(wash2_volume, wash2_res["A1"].bottom(z=RESERVOIR_ASP_Z))
        pip.air_gap(AIR_GAP)
        pip.dispense(
            wash2_volume + AIR_GAP, filter_plate["A1"].top(z=DISPENSE_ABOVE_Z)
        )
        pip.blow_out(filter_plate["A1"].top(z=DISPENSE_ABOVE_Z))
        if wash2_volume == WASH2_VOLUME_A:
            vacuum(ctx, vm_mod, vac_pressure, vac_time)

    pip.drop_tip(waste_chute)
    vacuum(ctx, vm_mod, vac_pressure, vac_time)

    ctx.comment(">> STEP 5: dry the membranes")
    vacuum(ctx, vm_mod, dry_pressure, dry_time)

    # =================================================================
    # REPLENISH TIPS FOR ELUTION
    # =================================================================
    # Only 3 on-deck tip racks now (C1 is the heater block), but 4
    # full-rack pickups are needed. Discard the spent wash-2 rack via the
    # waste chute, then gripper the spare rack from its C4 staging slot
    # onto the freed D1 adapter. A true off-deck stash can't be
    # gripper-fed (off-deck moves require use_gripper=False), which is
    # why the spare lives in a staging-area slot instead.
    ctx.comment(">> Swapping in spare tip rack for elution (C1 unavailable)")
    ctx.move_labware(tips_d1, waste_chute, use_gripper=True)
    ctx.move_labware(spare_tips, tiprack_adapters["D1"], use_gripper=True)

    # =================================================================
    # STEP 6 - REPOSITION FOR ELUTION
    # =================================================================
    # The elution plate is collected by physically moving it beneath the
    # filter plate, not by reversing vacuum flow.
    ctx.comment(">> STEP 6: reposition filter plate over the elution plate")

    # Collar (carrying the filter plate) off to the dock at A4.
    ctx.move_labware(manifold_collar, vm_mod.manifold_dock, use_gripper=True)
    # Spacer + elution plate onto the manifold.
    ctx.move_labware(tall_spacer, vm_mod, use_gripper=True)
    # Filter plate onto the elution plate.
    ctx.move_labware(filter_plate, elution_plate, use_gripper=True)
    # Collar back down over the stack.
    ctx.move_labware(manifold_collar, vm_mod, use_gripper=True)

    # =================================================================
    # STEP 7 - ELUTION                       (kit manual step 13)
    # =================================================================
    ctx.comment(f">> STEP 7: elute in {elution_volume:.0f} uL")
    pip.pick_up_tip(spare_tips["A1"])  # explicit: spare_tips isn't in pip's tracked tip_racks
    pip.flow_rate.aspirate = FR_WATER_ASP
    pip.flow_rate.dispense = FR_WATER_DISP
    pip.flow_rate.blow_out = FR_ELUTION_BLOWOUT

    pip.aspirate(elution_volume, water_res["A1"].bottom(z=RESERVOIR_ASP_Z))
    pip.air_gap(AIR_GAP_ELUTION)
    pip.dispense(
        elution_volume + AIR_GAP_ELUTION,
        filter_plate["A1"].bottom(z=ELUTION_DISPENSE_Z),
    )
    pip.blow_out(filter_plate["A1"].bottom(z=ELUTION_DISPENSE_Z))
    pip.drop_tip(waste_chute)

    hold(elution_incubation)
    vacuum(ctx, vm_mod, vac_pressure, vac_time)

    # =================================================================
    # DONE
    # =================================================================
    ctx.comment("=" * 68)
    ctx.comment("Extraction complete. Eluate is in the Elution Plate on the")
    ctx.comment("manifold (slot A3, under the collar and filter plate).")
    ctx.comment("")
    ctx.comment("NEXT, OFF-DECK - kit manual step 14:")
    ctx.comment("  Transfer the eluate to the prepared Silicon-A-HRC Plate")
    ctx.comment("  mounted on a NEW Elution Plate and centrifuge at exactly")
    ctx.comment("  3,500 x g for 3 minutes. Do not skip this for faecal or")
    ctx.comment("  soil samples -- it is the kit's inhibitor removal step.")
    ctx.comment("=" * 68)
