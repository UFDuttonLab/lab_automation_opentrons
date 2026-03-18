from opentrons import protocol_api
from opentrons import types

metadata = {
    'protocolName': 'NEBNext Ultra II FS DNA Library Prep V18 — 8-96 Samples (Section 2: >=100ng)',
    'author': 'Dutton Lab',
    'description': 'NEBNext Ultra II FS for 8-96 samples on Batman (Flex #1). Two-step size '
                   'selection via transfer plate. All 8-ch ALL-mode. Reagents on 200uL '
                   'plate (temp module) + 12-well reservoir. Auto-reload pauses for tips. '
                   'V16-V18: drop_offset z=-1 on mag block moves, two-pass removal with '
                   '200 µL over-aspiration, bead_loc sup at z=0.5 center (no XY offset), '
                   'reagent aspirations at bottom(0.5), temp module adapter removed from '
                   'API stack. All V15 changes retained.'
}

requirements = {'robotType': 'Flex', 'apiLevel': '2.20'}

# ============================================================================
# DECK LAYOUT (initial load for Phase 1)
#
#        1               2                  3             4 (staging — gripper only)
#   A: [  TC MODULE  ]  plate staging      p50_rack_1    p50_reserve
#   B: [  TC MODULE  ]  reservoir          200uL_rack_1  200uL_reserve
#   C: temp_mod+reagent  200uL_rack_2      p50_rack_2    transfer_plate
#   D: mag_block         waste_reservoir   [WASTE CHUTE] (no D4 staging)
#
# Tip strategy:
#   On-deck (pipette-accessible): 2 p50 (A3, C3) + 2 200µL (B3, C2) = 24 cols each
#   Reserve (staging, gripper-swap): 1 p50 (A4) + 1 200µL (B4)
#   When a 3rd rack is needed, gripper discards spent rack → waste chute,
#   then moves reserve from staging → empty working slot.
#   This gives up to 36 cols per pipette type between pauses.
#
# After each pause, user swaps ALL physical racks; tip counters reset.
#
# Plates: sample_plate (TC), reagent_plate (temp mod, 200uL — cols 1-7),
#         transfer_plate (C4 staging),
#         primer_plate (OFF_DECK → user places at Pause 2),
#         pcr_plate (OFF_DECK → user places at Pause 2),
#         output_plate (OFF_DECK → user places at Pause 3)
#
# Reagent plate layout (temp module, 4°C):
#   Col 1: ERAT (FS Enzyme + Buffer)
#   Col 2: NEBNext Adaptor (diluted)
#   Col 3: USER enzyme
#   Col 4-5: Ligation MM + Enhancer (2 cols for ≤6 / >6 sample columns)
#   Col 6-7: Q5 Master Mix (2 cols for ≤6 / >6 sample columns)
#
# Reservoir layout (NEST 12-well, B2):
#   A1: AMPure XP beads
#   A2-A3: (empty — Lig MM and Q5 MM moved to reagent plate in V14)
#   A4: 0.1X TE (RSB)
#   A5-A8: 80% EtOH (4 wells)
# ============================================================================


def add_parameters(parameters: protocol_api.Parameters):
    parameters.add_int(
        variable_name="NUM_SAMPLES", display_name="Number of Samples",
        description="Number of samples (must be a multiple of 8). Each column = 8 samples.",
        default=96,
        choices=[{"display_name": f"{n} samples ({n // 8} col{'s' if n > 8 else ''})",
                  "value": n} for n in range(8, 104, 8)])
    parameters.add_int(
        variable_name="FRAG_TIME", display_name="Fragmentation Time (min)",
        description="Time at 37C for fragmentation (NEB 2.1.5).", default=15,
        choices=[{"display_name": f"{m} min", "value": m}
                 for m in [5, 10, 15, 20, 25, 30, 35, 40]])
    parameters.add_int(
        variable_name="PCR_CYCLES", display_name="PCR Cycles",
        description="Number of PCR enrichment cycles (NEB Table 2.4.1).", default=4,
        choices=[{"display_name": f"{c} cycles", "value": c}
                 for c in [3, 4, 5, 6, 7, 8, 10, 12]])
    parameters.add_bool(
        variable_name="DRYRUN", display_name="Dry Run",
        description="Skip incubations and temp control. Movements run normally.",
        default=False)


def run(protocol: protocol_api.ProtocolContext):
    # ── PARAMETERS ────────────────────────────────────────────────────
    FRAG_TIME  = protocol.params.FRAG_TIME
    PCR_CYCLES = protocol.params.PCR_CYCLES
    DRYRUN     = protocol.params.DRYRUN
    NUM_COL    = protocol.params.NUM_SAMPLES // 8   # 1-12 columns

    # Column well names for 8-channel access (A-row wells address the whole column)
    COLS = [f'A{i}' for i in range(1, NUM_COL + 1)]

    protocol.comment(f"Running {protocol.params.NUM_SAMPLES} samples ({NUM_COL} columns)")

    # ── WASTE CHUTE ─────────────────────────────────────────────────
    waste_chute = protocol.load_waste_chute()

    # ── MODULES ─────────────────────────────────────────────────────
    thermocycler = protocol.load_module('thermocyclerModuleV2')
    sample_plate = thermocycler.load_labware('opentrons_96_wellplate_200ul_pcr_full_skirt')

    mag_block = protocol.load_module('magneticBlockV1', 'D1')

    temp_mod = protocol.load_module('temperature module gen2', 'C1')
    # V18: Use 'opentrons_96_pcr_adapter' (not the older
    # 'opentrons_96_well_aluminum_block'). The block definition adds
    # 18.16 mm to the Z-stack vs 13.85 mm for the PCR adapter — a 4.3 mm
    # difference that prevented tips from reaching the well bottom.
    # The PCR adapter correctly accounts for the plate nesting into the block.
    temp_adapter = temp_mod.load_adapter('opentrons_96_pcr_adapter')
    reagent_plate = temp_adapter.load_labware(
        'opentrons_96_wellplate_200ul_pcr_full_skirt')

    # ── RESERVOIRS ──────────────────────────────────────────────────
    reservoir = protocol.load_labware('nest_12_reservoir_15ml', 'B2')
    waste_res = protocol.load_labware('nest_1_reservoir_195ml', 'D2')
    TRASH = waste_res['A1']

    # ── STAGING LABWARE (col 4 — gripper only, no pipette access) ──
    transfer_plate = protocol.load_labware(
        'opentrons_96_wellplate_200ul_pcr_full_skirt', 'C4')

    # Plates placed mid-protocol by user (no D4 staging slot available)
    primer_plate = protocol.load_labware(
        'opentrons_96_wellplate_200ul_pcr_full_skirt', protocol_api.OFF_DECK)
    pcr_plate = protocol.load_labware(
        'opentrons_96_wellplate_200ul_pcr_full_skirt', protocol_api.OFF_DECK)
    output_plate = protocol.load_labware(
        'opentrons_96_wellplate_200ul_pcr_full_skirt', protocol_api.OFF_DECK)

    # ── TIP RACKS ───────────────────────────────────────────────────
    # On-deck (pipette-accessible): 2 p50 + 2 200µL (for p1000) = 24 cols each
    p50_rack_1 = protocol.load_labware('opentrons_flex_96_tiprack_50ul', 'A3')
    p50_rack_2 = protocol.load_labware('opentrons_flex_96_tiprack_50ul', 'C3')
    p1000_rack_1 = protocol.load_labware('opentrons_flex_96_tiprack_200ul', 'B3')
    p1000_rack_2 = protocol.load_labware('opentrons_flex_96_tiprack_200ul', 'C2')

    # Reserve racks (staging — gripper swaps them into working slots as needed)
    p50_reserve = protocol.load_labware('opentrons_flex_96_tiprack_50ul', 'A4')
    p1000_reserve = protocol.load_labware('opentrons_flex_96_tiprack_200ul', 'B4')

    # ── PIPETTES ────────────────────────────────────────────────────
    p1000 = protocol.load_instrument('flex_8channel_1000', 'left')
    p50   = protocol.load_instrument('flex_8channel_50',   'right')

    # ── TIP TRACKING WITH AUTO-SWAP ─────────────────────────────────
    # Tracks column index within current rack and rack progression.
    # When both on-deck racks are spent (col 24 reached), gripper discards
    # the first spent rack to waste chute and moves the reserve into its slot.

    class TipTracker:
        """Manage tip racks for one pipette with gripper-swap of staging reserve.

        Two on-deck racks (pipette-accessible) + one reserve in staging
        (gripper-only). When both on-deck racks are spent, the gripper
        discards the first and brings in the reserve automatically.

        At each pause, reset() does ONE manual-move to relocate the
        discarded rack from waste back to staging, then swaps roles
        so rack iteration starts fresh with objects already on-deck.
        """
        def __init__(self, rack_1, rack_2, reserve, slot_1, staging_slot, pip_name):
            self.rack_1 = rack_1          # initially at slot_1 (on-deck)
            self.rack_2 = rack_2          # initially at slot_2 (on-deck)
            self.reserve = reserve        # initially at staging_slot
            self.slot_1 = slot_1          # string: rack_1's on-deck slot
            self.staging_slot = staging_slot  # string: reserve's staging slot
            self.name = pip_name
            self._init_phase()

        def _init_phase(self):
            self.racks = [self.rack_1, self.rack_2]
            self.rack_idx = 0
            self.col = 0
            self.swapped = False

        def get_tip(self):
            if self.col >= 12:
                self.col = 0
                self.rack_idx += 1
            if self.rack_idx >= len(self.racks):
                if not self.swapped:
                    protocol.move_labware(
                        self.rack_1, waste_chute, use_gripper=True)
                    protocol.move_labware(
                        self.reserve, self.slot_1, use_gripper=True)
                    self.racks.append(self.reserve)
                    self.swapped = True
                else:
                    raise RuntimeError(
                        f"Out of {self.name} tips! Need a pause to reload.")
            tip = self.racks[self.rack_idx].wells()[self.col * 8]
            self.col += 1
            return tip

        def cols_remaining(self):
            """Return number of tip columns still available before exhaustion."""
            total_racks = len(self.racks) + (0 if self.swapped else 1)  # include reserve
            total_cols = total_racks * 12
            used_cols = self.rack_idx * 12 + self.col
            return total_cols - used_cols

        def reset(self):
            """Reset after user physically replaces all racks at a pause.

            After a swap: rack_1 is in waste, reserve is at slot_1.
            We do ONE move_labware(use_gripper=False) to send rack_1
            from waste → staging_slot, then swap the object roles.
            The user must place fresh physical racks at all API locations.
            """
            if self.swapped:
                # rack_1 is in waste → move it to staging (1 manual-move prompt)
                protocol.move_labware(
                    self.rack_1, self.staging_slot, use_gripper=False)
                # Swap roles: reserve (now at slot_1) becomes rack_1,
                # old rack_1 (now at staging) becomes reserve
                self.rack_1, self.reserve = self.reserve, self.rack_1
            self._init_phase()

    p50_tips = TipTracker(
        p50_rack_1, p50_rack_2, p50_reserve, 'A3', 'A4', 'p50')
    p1000_tips = TipTracker(
        p1000_rack_1, p1000_rack_2, p1000_reserve, 'B3', 'B4', 'p1000')

    def reset_tips():
        p50_tips.reset()
        p1000_tips.reset()

    def _p1000_cols_remaining():
        return p1000_tips.cols_remaining()

    def _p50_cols_remaining():
        return p50_tips.cols_remaining()

    # ── REAGENT LOCATIONS ───────────────────────────────────────────
    ERAT    = reagent_plate.columns_by_name()['1']   # pre-mixed FS Enzyme + Buffer
    ADAPTOR = reagent_plate.columns_by_name()['2']   # NEBNext Adaptor (15 uM)
    USER_E  = reagent_plate.columns_by_name()['3']   # USER enzyme

    # V14: Ligation MM and Q5 MM moved from reservoir to reagent plate.
    # Two columns each — eliminates dead-volume problems in 15 mL reservoir
    # at low sample counts (NEB kit provides zero excess for these reagents).
    # ≤6 sample columns → all reagent from col A; >6 → first 6 from A, rest from B.
    LIG_MM_A = reagent_plate.columns_by_name()['4']  # Lig MM + Enhancer, first half
    LIG_MM_B = reagent_plate.columns_by_name()['5']  # Lig MM + Enhancer, second half
    Q5_MM_A  = reagent_plate.columns_by_name()['6']  # Q5 Master Mix, first half
    Q5_MM_B  = reagent_plate.columns_by_name()['7']  # Q5 Master Mix, second half

    def _lig_mm_well(col_idx):
        """Return the correct Lig MM reagent well for sample column index (0-based)."""
        return LIG_MM_A[0] if col_idx < 6 else LIG_MM_B[0]

    def _q5_mm_well(col_idx):
        """Return the correct Q5 MM reagent well for sample column index (0-based)."""
        return Q5_MM_A[0] if col_idx < 6 else Q5_MM_B[0]

    BEADS  = reservoir['A1']    # AMPure XP beads
    RSB    = reservoir['A4']    # 0.1X TE (Tris-EDTA)
    ETOH   = [reservoir[f'A{i}'] for i in range(5, 9)]  # 4 wells, ~22 mL each

    # ── LIQUID DEFINITIONS ────────────────────────────────────────────
    # Volumes assume worst-case (12 columns / 96 samples) + ~20% dead volume.
    # For fewer samples, reagent volumes scale down but dead volume stays.

    # Reagent plate liquids (temp module, 4°C)
    liq_erat = protocol.define_liquid(
        name="FS Enzyme Mix + FS Reaction Buffer",
        description="NEBNext Ultra II FS Enzyme Mix + Buffer, pre-mixed (9 µL/rxn)",
        display_color="#E63946")  # red
    liq_adaptor = protocol.define_liquid(
        name="NEBNext Adaptor (diluted)",
        description="NEBNext Adaptor for Illumina, diluted per NEB Table 2.1 (2.5 µL/rxn)",
        display_color="#F4A261")  # orange
    liq_user = protocol.define_liquid(
        name="USER Enzyme",
        description="USER enzyme for adaptor loop cleavage (3 µL/rxn)",
        display_color="#2A9D8F")  # teal
    liq_lig_mm = protocol.define_liquid(
        name="Ligation Master Mix + Enhancer",
        description="NEBNext Ligation MM + Ligation Enhancer, pre-mixed (31 µL/rxn). "
                    "Cols 4-5 of reagent plate.",
        display_color="#5C6BC0")  # indigo
    liq_q5_mm = protocol.define_liquid(
        name="NEBNext Q5 Master Mix",
        description="Q5 Ultra II Master Mix for PCR enrichment (25 µL/rxn). "
                    "Cols 6-7 of reagent plate.",
        display_color="#AB47BC")  # purple

    # Reservoir liquids
    liq_beads = protocol.define_liquid(
        name="AMPure XP Beads",
        description="Beckman Coulter AMPure XP beads, vortexed (Phases 2a, 2b, 4)",
        display_color="#6D4C41")  # brown
    liq_rsb = protocol.define_liquid(
        name="0.1X TE (RSB)",
        description="Resuspension buffer / 0.1X TE for bead elutions",
        display_color="#42A5F5")  # blue
    liq_etoh = protocol.define_liquid(
        name="80% Ethanol",
        description="Freshly prepared 80% ethanol for bead washes",
        display_color="#BDBDBD")  # gray

    # Waste reservoir
    liq_waste = protocol.define_liquid(
        name="Liquid Waste",
        description="Supernatant and wash waste collection",
        display_color="#212121")  # dark gray

    # Sample plate
    liq_gdna = protocol.define_liquid(
        name="Genomic DNA",
        description="Input gDNA samples (≥100 ng in 26 µL 0.1X TE per NEB Section 2)",
        display_color="#66BB6A")  # green

    # Load liquids into labware wells (Well.load_liquid — API 2.14+)
    # Reagent plate — all aspirations from column via 8-channel
    for well in reagent_plate.columns_by_name()['1']:
        well.load_liquid(liquid=liq_erat, volume=9 * NUM_COL + 10)
    for well in reagent_plate.columns_by_name()['2']:
        well.load_liquid(liquid=liq_adaptor, volume=2.5 * NUM_COL + 5)
    for well in reagent_plate.columns_by_name()['3']:
        well.load_liquid(liquid=liq_user, volume=3 * NUM_COL + 5)

    # V14: Ligation MM and Q5 MM on reagent plate (2 cols each).
    # Each plate column serves up to 6 sample columns. Per-well volume =
    # vol_per_rxn × cols_served. At 96 samples: 186 µL/well (Lig) or
    # 150 µL/well (Q5) — both within 200 µL well capacity.
    lig_cols_a = min(NUM_COL, 6)
    lig_cols_b = max(NUM_COL - 6, 0)
    q5_cols_a  = min(NUM_COL, 6)
    q5_cols_b  = max(NUM_COL - 6, 0)
    for well in LIG_MM_A:
        well.load_liquid(liquid=liq_lig_mm, volume=31 * lig_cols_a)
    if lig_cols_b > 0:
        for well in LIG_MM_B:
            well.load_liquid(liquid=liq_lig_mm, volume=31 * lig_cols_b)
    for well in Q5_MM_A:
        well.load_liquid(liquid=liq_q5_mm, volume=25 * q5_cols_a)
    if q5_cols_b > 0:
        for well in Q5_MM_B:
            well.load_liquid(liquid=liq_q5_mm, volume=25 * q5_cols_b)

    # Reservoir — single-trough wells, volumes in µL
    # AMPure: Phases 2a (40/col), 2b (20/col), 4 (45/col) = 105 µL/col × 8 wells
    BEADS.load_liquid(liquid=liq_beads, volume=105 * NUM_COL * 8 + 500)
    # RSB: 28.5 + 17 + 33 = 78.5 µL/rxn across phases + 1200 µL dead volume
    RSB.load_liquid(liquid=liq_rsb, volume=int(78.5 * NUM_COL * 8 + 1200))
    # EtOH: ~200 µL × N cols × 2 washes × multiple phases, split across 4 wells
    for well in ETOH:
        well.load_liquid(liquid=liq_etoh, volume=15000)

    # Waste reservoir
    TRASH.load_liquid(liquid=liq_waste, volume=0)

    # Sample plate — gDNA 26 µL per well
    for i in range(1, NUM_COL + 1):
        for r in 'ABCDEFGH':
            sample_plate[f'{r}{i}'].load_liquid(liquid=liq_gdna, volume=26)

    _etoh_idx = 0
    def next_etoh():
        nonlocal _etoh_idx
        well = ETOH[_etoh_idx % len(ETOH)]
        _etoh_idx += 1
        return well

    # ── PLATE MOVEMENT HELPERS ──────────────────────────────────────
    MAG_STAGING = 'A2'
    _tc_lid_open = False

    def tc_open():
        nonlocal _tc_lid_open
        if not _tc_lid_open:
            thermocycler.open_lid()
            _tc_lid_open = True

    def tc_close():
        nonlocal _tc_lid_open
        if _tc_lid_open:
            thermocycler.close_lid()
            _tc_lid_open = False

    # ── PLATE LOCATION TRACKING ───────────────────────────────────────
    # Simple string dictionary: plate_object → 'thermocycler'|'staging'|'magnet'
    # Using the plate object directly as key (not id()) for stability.
    _plate_at = {}
    _plate_at[sample_plate] = 'thermocycler'

    def move_plate(plate, target):
        """Move plate between thermocycler / staging / magnet via gripper."""
        if _plate_at.get(plate) == target:
            return  # already there

        dest = {'thermocycler': thermocycler,
                'magnet': mag_block,
                'staging': MAG_STAGING}[target]

        if _plate_at.get(plate) == 'thermocycler':
            if not DRYRUN:
                thermocycler.deactivate_block()
            tc_open()
        if target == 'thermocycler':
            tc_open()

        # V16: drop_offset seats the plate flush on the magnetic block.
        # Without it, the plate sits ~1 mm too high and all Z-heights
        # are offset — tips float above the liquid during aspiration.
        # Matches the working Zymo Magbead Extraction protocol.
        if target == 'magnet':
            protocol.move_labware(plate, dest, use_gripper=True,
                                  drop_offset={"x": 0, "y": 0, "z": -1})
        else:
            protocol.move_labware(plate, dest, use_gripper=True)
        _plate_at[plate] = target

    def bead_loc(well, mode):
        """Precise positions for bead work on the magnetic block."""
        if mode == 'bead':
            return well.bottom(0.2)
        if mode == 'sup':
            # z=0.5, center of well, no XY offset.
            # V16: lowered from z=1.0 to z=0.5 — after Pass 1 bulk removal,
            # residual is ~10-15 µL (0.4-0.6 mm deep). z=1.0 floats above it.
            # Removed x=0.5 offset — the magnetic block pulls beads to the
            # side WALL, not the bottom. The well floor is clear. Aspiration
            # should be straight down the center, matching the Zymo protocol.
            return well.bottom(0.5)
        return well.bottom(7)  # 'dispense' — mid-wall

    # ── BEAD WASH HELPER ────────────────────────────────────────────
    def etoh_wash(plate, cols, n_washes=2):
        """Two EtOH washes. Plate must be on magnet.

        Tip strategy per wash cycle:
        - 1 shared 200uL tip adds 185 uL EtOH to ALL columns
          (tip only contacts EtOH reservoir + well top at z=top(-2),
          never contacts sample or bead pellet)
        - 30 s soak with all columns filled simultaneously
        - N fresh 200uL tips remove EtOH (one per column, contacts sample)

        After the final wash, N fresh p50 tips do an ultra-slow residual
        sweep at bottom(0.2) to remove the 5-10 uL the p200 tip cannot reach.

        Total: (1 + N) × 2 p1000 + N p50 = 2N+2 p1000 + N p50 per cleanup.

        Between washes, a pause-and-reload is triggered if the p1000 tip
        tracker has fewer than N+1 columns remaining.

        NEB 2.3.10-11 / 2.5.5-7
        """
        n = len(cols)
        for i in range(n_washes):
            # Check if we have enough p1000 tips for this wash cycle (1 + N)
            tips_remaining = _p1000_cols_remaining()
            if tips_remaining < n + 1:
                protocol.pause(
                    f"Need {n+1} 200µL tip columns for EtOH wash {i+1} "
                    f"but only {tips_remaining} remain.\n"
                    "1. Remove spent 200µL racks from deck and staging.\n"
                    "2. Load 2x fresh 200µL racks (B3, C2).\n"
                    "   Load 1x 200µL reserve rack on B4 (staging).\n"
                    "3. Press Resume.")
                p1000_tips.reset()

            ew = next_etoh()
            # -- Add EtOH to all columns (ONE shared tip) --
            # V12: 185 µL (reduced from 200 to prevent splash/overflow in
            # 200 µL PCR wells). Dispense at rate=0.5.
            # V14: Raised dispense from bottom(7) to top(-2). The p200 tip
            # was dipping into already-dispensed EtOH, contaminating the
            # shared tip. EtOH is low-viscosity and runs down the wall fine
            # from top(-2). Follows agent instructions Section 9.5 pattern.
            p1000.pick_up_tip(p1000_tips.get_tip())
            for c in cols:
                p1000.aspirate(185, ew)
                p1000.dispense(185, plate[c].top(-2), rate=0.5)
            p1000.drop_tip()
            if not DRYRUN:
                protocol.delay(seconds=30)

            # -- Remove EtOH: two-pass pattern (matches Zymo protocol) --
            # Pass 1: bulk removal. Pass 2: sweep with 200 µL over-aspiration
            # (pulls residual + air). Dispense to waste between passes so
            # tip is empty with full suction force on the second descent.
            for c in cols:
                p1000.pick_up_tip(p1000_tips.get_tip())
                # Pass 1: bulk
                p1000.move_to(plate[c].center())
                p1000.aspirate(185, plate[c].bottom(3), rate=0.3)
                p1000.move_to(plate[c].top())
                p1000.dispense(185, TRASH)
                p1000.blow_out(TRASH.top())
                # Pass 2: sweep (200 µL from z=1, gets residual + air)
                p1000.move_to(plate[c].center())
                p1000.aspirate(200, bead_loc(plate[c], 'sup'), rate=0.15)
                p1000.move_to(plate[c].top())
                p1000.dispense(200, TRASH)
                p1000.blow_out(TRASH.top())
                if i == n_washes - 1:
                    # Final wash: aggressive residual removal with p1000
                    protocol.delay(seconds=5)
                    p1000.aspirate(50, plate[c].bottom(0.2), rate=0.15)
                    p1000.dispense(50, TRASH)
                    p1000.blow_out(TRASH.top())
                p1000.drop_tip()

        # -- V14: p50 residual sweep after final wash --
        # The p200 tip OD is too wide to reach the bottom of a 0.2 mL PCR
        # well without touching the walls and disturbing the bead pellet.
        # The p50 tip is narrower and can aspirate at bottom(0.2) safely.
        # V15: Raised from bottom(0) to bottom(0.2) — at z=0 the tip was
        # pressed flush against the well floor creating a vacuum seal,
        # preventing any liquid uptake despite visible residual in wells.
        # z=0.2 gives enough clearance for liquid to flow under the tip.
        p50.configure_for_volume(20)
        for c in cols:
            p50.pick_up_tip(p50_tips.get_tip())
            p50.aspirate(20, plate[c].bottom(0.2), rate=0.1)
            p50.dispense(20, TRASH)
            p50.blow_out(TRASH.top())
            p50.drop_tip()

    # ════════════════════════════════════════════════════════════════
    #                        PROTOCOL EXECUTION
    # ════════════════════════════════════════════════════════════════

    # Pre-chill modules
    if not DRYRUN:
        thermocycler.set_block_temperature(4)
        thermocycler.set_lid_temperature(75)
        temp_mod.set_temperature(4)

    # ================================================================
    # PHASE 1: ENZYMATIC STEPS (NEB 2.1-2.2)
    # Tips needed: 4×N p50 + 0 p1000  (max 48 p50 at N=12)
    # p50: 36 avail per phase (auto-reload during ligation incub if N≥10)
    # p1000: not used in Phase 1 (Lig MM switched to p50 in V14)
    # ================================================================
    protocol.comment("══ PHASE 1: ENZYMATIC STEPS ══")

    # ── 1. ERAT: 9 uL (NEB 2.1.3-2.1.4) ──
    # Keep plate on thermocycler at 4°C during ERAT addition (cold surface)
    tc_open()  # lid must be open for pipette access
    p50.configure_for_volume(9)
    for c in COLS:
        p50.pick_up_tip(p50_tips.get_tip())
        p50.aspirate(9 + 3, ERAT[0].bottom(0.5), rate=0.5)
        p50.dispense(3, ERAT[0].bottom(1), rate=0.5)
        protocol.delay(seconds=3)
        p50.dispense(9, sample_plate[c])
        p50.mix(10, 20, sample_plate[c].bottom(0.5))
        p50.blow_out(sample_plate[c].top(-2))
        p50.drop_tip()
    # Hold at 4°C for 2 min after ERAT addition (equilibrate before ramp)
    tc_close()
    if not DRYRUN:
        thermocycler.execute_profile(
            steps=[{'temperature': 4,  'hold_time_minutes': 2},
                   {'temperature': 37, 'hold_time_minutes': FRAG_TIME},
                   {'temperature': 65, 'hold_time_minutes': 30}],
            repetitions=1, block_max_volume=50)
        thermocycler.set_block_temperature(4)  # hold at 4C until plate moved

    # ── 2. ADAPTOR LIGATION (NEB 2.2.1) ──
    move_plate(sample_plate, 'staging')

    # 2a. Adaptor: 2.5 uL — CRITICAL: configure_for_volume for sub-5 uL accuracy
    p50.configure_for_volume(2.5)
    for c in COLS:
        p50.pick_up_tip(p50_tips.get_tip())
        p50.aspirate(2.5 + 3, ADAPTOR[0].bottom(0.5), rate=0.5)
        p50.dispense(3, ADAPTOR[0].bottom(1), rate=0.5)
        protocol.delay(seconds=3)
        p50.dispense(2.5, sample_plate[c].bottom(0.2), rate=0.5) #changed from top -1 to bottom 0.2
        p50.blow_out(sample_plate[c].bottom(0.2)) #changed from top -2 to bottom 0.2
        p50.drop_tip()

    # 2b. Ligation Master Mix + Enhancer: 31 uL (30+1, pre-mixed)
    # NEB 2.2.2: "pipette up and down at least 10 times to mix thoroughly"
    # V14: Lig MM moved from reservoir to reagent plate cols 4-5.
    #       Switched from p1000 to p50 for accuracy at 34 µL (68% of p50 range).
    #       p1000+p200 tip was under-delivering ~25 µL on a 34 µL aspiration.
    # Source-well mix volume is dynamic: 70% of remaining volume in that well,
    # capped at 40 µL (p50 tip safety) and floored at 20 µL.
    # At 8 samp (1 col): well has 31 µL → mix at 21 µL.
    # At 96 samp (12 cols): well starts at 186 µL → mix at 40 µL (capped).
    p50.configure_for_volume(31)
    for i, c in enumerate(COLS):
        lig_well = _lig_mm_well(i)
        cols_used = i if i < 6 else i - 6
        cols_this_well = min(NUM_COL, 6) if i < 6 else max(NUM_COL - 6, 0)
        lig_remaining = 31 * (cols_this_well - cols_used)
        lig_mix_vol = int(min(40, max(20, lig_remaining * 0.7)))
        p50.pick_up_tip(p50_tips.get_tip())
        p50.mix(3, lig_mix_vol, lig_well.bottom(0.5))
        p50.aspirate(31 + 3, lig_well.bottom(0.5), rate=0.5)
        p50.dispense(3, lig_well.bottom(1), rate=0.5)
        p50.move_to(lig_well.top(-2))       # gentle withdraw (replaces touch_tip)
        protocol.delay(seconds=3)            # let hanging drop fall back
        p50.dispense(31, sample_plate[c], rate=0.5)
        p50.mix(15, 40, sample_plate[c].bottom(0.5), rate=0.5)
        p50.blow_out(sample_plate[c].bottom(0.5))
        p50.drop_tip()

    # ── Ligation incubation: 20C / 15 min, lid OFF (NEB 2.2.3) ──
    move_plate(sample_plate, 'thermocycler')
    if not DRYRUN:
        thermocycler.deactivate_lid()
        thermocycler.set_block_temperature(20, hold_time_minutes=15)

    # V14: Check p50 availability for USER enzyme (N cols needed).
    # After ERAT + Adaptor + Lig MM, we've used 3N p50 cols.
    # At N≥10 (80+ samples), the 36-col capacity is insufficient for USER.
    # Reload during ligation incubation — zero added time.
    p50_needed = NUM_COL
    p50_avail = _p50_cols_remaining()
    if p50_avail < p50_needed:
        protocol.pause(
            f"Ligation incubating (20°C, 15 min). Need {p50_needed} p50 tip "
            f"columns for USER but only {p50_avail} remain.\n"
            "1. Remove spent p50 racks from deck and staging.\n"
            "2. Load 2x fresh p50 racks (A3, C3).\n"
            "   Load 1x p50 reserve rack on A4 (staging).\n"
            "3. Press Resume.")
        p50_tips.reset()

    # ── 3. USER ENZYME: 3 uL (NEB 2.2.4-2.2.5) ──
    move_plate(sample_plate, 'staging')
    p50.configure_for_volume(3)
    for c in COLS:
        p50.pick_up_tip(p50_tips.get_tip())
        p50.aspirate(3 + 3, USER_E[0].bottom(0.5), rate=0.5)
        p50.dispense(3, USER_E[0].bottom(1), rate=0.5)
        protocol.delay(seconds=3)
        p50.dispense(3, sample_plate[c])
        # Reconfigure to default mode — low-volume mode max is ~30 µL,
        # too small for the 50 µL mix. Tip is empty after dispense.
        p50.configure_for_volume(50)
        p50.mix(10, 50, sample_plate[c].bottom(0.5))
        p50.blow_out(sample_plate[c].top(-2))
        p50.drop_tip()

    # ── USER incubation: 37C / 15 min, lid >= 47C (NEB 2.2.5) ──
    move_plate(sample_plate, 'thermocycler')
    tc_close()
    if not DRYRUN:
        thermocycler.set_lid_temperature(47)
        thermocycler.set_block_temperature(37, hold_time_minutes=15)

    # ════════════════════════════════════════════════════════════════
    # PAUSE 1: Reload tips DURING USER incubation (zero added time)
    # ════════════════════════════════════════════════════════════════
    protocol.pause(
        "USER incubation running (37C, 15 min). While you wait:\n"
        "1. Remove ALL spent tip racks from deck and staging.\n"
        "2. Load 2x p50 racks (A3, C3).\n"
        "   Load 2x 200µL racks (B3, C2).\n"
        "   Load 1x 200µL reserve rack on B4 (staging).\n"
        "3. Press Resume when loaded.")
    reset_tips()

    # ================================================================
    # PHASE 2a: SIZE SELECTION — FIRST BEAD BIND (NEB 2.3)
    # Tips needed: 2×N p50 + 2×N p1000 (max 24+24 at N=12)
    # V12: RSB and bead transfers switched to p50 for accuracy.
    # p50: 24 on-deck (2 racks) ✓
    # p1000: 24 on-deck (2 racks) + reserve if needed ✓
    # ================================================================
    protocol.comment("══ PHASE 2a: SIZE SELECTION — FIRST BEAD BIND ══")

    # ── 4. Add RSB to 100 uL total (NEB 2.3.1) ──
    # V12: p50 for accurate 28.5 µL transfer (p1000 was 3-8 µL off at this volume).
    # Mix reduced from 60→50 µL to stay within p50 max. RSB.bottom(1) explicit.
    move_plate(sample_plate, 'staging')
    p50.configure_for_volume(28.5)
    for c in COLS:
        p50.pick_up_tip(p50_tips.get_tip())
        p50.aspirate(28.5 + 3, RSB.bottom(1))
        p50.dispense(3, RSB.bottom(1))
        protocol.delay(seconds=3)
        p50.dispense(28.5, sample_plate[c])
        p50.mix(10, 50, sample_plate[c].bottom(0.5))
        p50.blow_out(sample_plate[c].top(-2))
        p50.drop_tip()

    # ── 5. First bead bind: 40 uL = 0.4× (NEB 2.3.2) ──
    # V12: p50 for accurate 40 µL bead transfer. p1000 reused for reservoir
    # mix (before) and well mix (after) — same tip, same reagent (AMPure).
    p50.configure_for_volume(40)
    for c in COLS:
        p1000.pick_up_tip(p1000_tips.get_tip())
        p1000.mix(5, 100, BEADS)
        # p1000 holds tip while p50 does accurate bead transfer
        p50.pick_up_tip(p50_tips.get_tip())
        p50.aspirate(40 + 3, BEADS, rate=0.5)
        p50.dispense(3, BEADS.bottom(1), rate=0.5)
        protocol.delay(seconds=3)
        p50.dispense(40, sample_plate[c])
        p50.drop_tip()
        # p1000 mixes beads into sample (100 µL exceeds p50 max)
        p1000.mix(10, 100, sample_plate[c].bottom(0.5))
        p1000.blow_out(sample_plate[c].top(-2))
        p1000.drop_tip()

    if not DRYRUN:
        protocol.delay(minutes=5, msg="Bead bind 5 min RT (NEB 2.3.3)")
    move_plate(sample_plate, 'magnet')
    if not DRYRUN:
        protocol.delay(minutes=5, msg="Mag sep 5 min (NEB 2.3.4)")

    # ── 6. Transfer ~140 uL supernatant to transfer plate (NEB 2.3.5) ──
    # Two-pass pattern: bulk removal, dispense, then sweep with 200 µL
    # over-aspiration to guarantee complete recovery of precious supernatant.
    protocol.move_labware(transfer_plate, MAG_STAGING, use_gripper=True)
    _plate_at[transfer_plate] = 'staging'
    for c in COLS:
        p1000.pick_up_tip(p1000_tips.get_tip())
        # Pass 1: bulk
        p1000.move_to(sample_plate[c].center())
        p1000.aspirate(140, sample_plate[c].bottom(3), rate=0.25)
        p1000.move_to(sample_plate[c].top())
        p1000.dispense(140, transfer_plate[c].top(-2))
        p1000.blow_out(transfer_plate[c].top(-2))
        # Pass 2: sweep (200 µL from z=0.5, gets residual + air)
        p1000.move_to(sample_plate[c].center())
        p1000.aspirate(200, bead_loc(sample_plate[c], 'sup'), rate=0.1)
        p1000.move_to(sample_plate[c].top())
        p1000.dispense(200, transfer_plate[c].top(-2))
        p1000.blow_out(transfer_plate[c].top(-2))
        p1000.drop_tip()

    # Discard sample plate (large-fragment beads — waste)
    protocol.move_labware(sample_plate, waste_chute, use_gripper=True)
    _plate_at.pop(sample_plate, None)

    # ════════════════════════════════════════════════════════════════
    # PAUSE 1.5: Mid-size-selection tip swap (~2-3 min)
    # ════════════════════════════════════════════════════════════════
    protocol.pause(
        "First bead bind complete. Supernatant in transfer plate on A2.\n"
        "1. Remove spent tip racks from deck and staging.\n"
        "2. Load 2x p50 racks (A3, C3) + 2x 200µL racks (B3, C2).\n"
        "   Load 1x p50 reserve rack on A4 (staging).\n"
        "   Load 1x 200µL reserve rack on B4 (staging).\n"
        "3. Press Resume.")
    reset_tips()

    # ================================================================
    # PHASE 2b: SIZE SELECTION — SECOND BEAD BIND + WASH + ELUTE (NEB 2.3)
    # Tips needed: 4×N p50 + (2N+3 + 2N+2) p1000
    # At N=12: 48 p50 + 53 p1000
    # p50: 24 on-deck + 12 reserve = 36 (auto-reload before RSB if N≥10)
    # p1000: 36 on-deck+reserve for beads/sup/wash1, then auto-reload
    #        inside etoh_wash() when tips run low.
    # V14: +N p50 from residual EtOH sweep in etoh_wash.
    # ================================================================
    protocol.comment("══ PHASE 2b: SECOND BEAD BIND + WASHES ══")

    # ── 7. Second bead bind: 20 uL beads (NEB 2.3.6) ──
    # Order: mix beads in reservoir → aspirate 20 uL → dispense → mix in well
    p50.configure_for_volume(20)
    for i, c in enumerate(COLS):
        # Mix beads in reservoir first (every 4 cols to keep suspended)
        if i % 4 == 0:
            p1000.pick_up_tip(p1000_tips.get_tip())
            p1000.mix(10, 100, BEADS)
            p1000.drop_tip()
        # Aspirate and dispense beads
        p50.pick_up_tip(p50_tips.get_tip())
        p50.aspirate(20 + 3, BEADS, rate=0.5)
        p50.dispense(3, BEADS.bottom(1), rate=0.5)
        protocol.delay(seconds=3)
        p50.dispense(20, transfer_plate[c])
        p50.drop_tip()
        # Mix beads into sample
        p1000.pick_up_tip(p1000_tips.get_tip())
        p1000.mix(10, 130, transfer_plate[c].bottom(0.5))
        p1000.blow_out(transfer_plate[c].top(-2))
        p1000.drop_tip()

    if not DRYRUN:
        protocol.delay(minutes=5, msg="Second bead bind 5 min RT (NEB 2.3.6)")

    protocol.move_labware(transfer_plate, mag_block, use_gripper=True,
                          drop_offset={"x": 0, "y": 0, "z": -1})
    _plate_at[transfer_plate] = 'magnet'
    if not DRYRUN:
        protocol.delay(minutes=5, msg="Mag sep 5 min (NEB 2.3.7)")

    # ── 8. Remove supernatant ~160 uL (NEB 2.3.8) ──
    # Two-pass pattern: bulk then sweep with over-aspiration.
    for c in COLS:
        p1000.pick_up_tip(p1000_tips.get_tip())
        # Pass 1: bulk
        p1000.move_to(transfer_plate[c].center())
        p1000.aspirate(160, transfer_plate[c].bottom(3), rate=0.25)
        p1000.move_to(transfer_plate[c].top())
        p1000.dispense(160, TRASH)
        p1000.blow_out(TRASH.top())
        # Pass 2: sweep (200 µL from z=1, gets residual + air)
        p1000.move_to(transfer_plate[c].center())
        p1000.aspirate(200, bead_loc(transfer_plate[c], 'sup'), rate=0.1)
        p1000.move_to(transfer_plate[c].top())
        p1000.dispense(200, TRASH)
        p1000.blow_out(TRASH.top())
        p1000.drop_tip()

    # ── 9. EtOH washes ×2 (NEB 2.3.9-2.3.10) ──
    etoh_wash(transfer_plate, COLS)

    # ── 10. Air dry (NEB 2.3.11) ──
    if not DRYRUN:
        protocol.delay(minutes=5, msg="Air dry beads up to 5 min (NEB 2.3.11)")

    # V14: Check p50 availability for RSB elution (2N cols needed).
    # At N≥10, the p50 sweep in etoh_wash may have exhausted reserves.
    p50_needed = 2 * NUM_COL
    p50_avail = _p50_cols_remaining()
    if p50_avail < p50_needed:
        protocol.pause(
            f"Need {p50_needed} p50 tip columns for RSB elution "
            f"but only {p50_avail} remain.\n"
            "1. Remove spent p50 racks from deck and staging.\n"
            "2. Load 2x fresh p50 racks (A3, C3).\n"
            "   Load 1x p50 reserve rack on A4 (staging).\n"
            "3. Press Resume.")
        p50_tips.reset()

    # ── 11. RSB elution: 17 uL (NEB 2.3.12-2.3.14) ──
    # Add RSB to pellet on magnet first, then move off for resuspension
    p50.configure_for_volume(17)
    for c in COLS:
        p50.pick_up_tip(p50_tips.get_tip())
        p50.aspirate(17 + 3, RSB.bottom(1))
        p50.dispense(3, RSB.bottom(1))
        protocol.delay(seconds=3)
        p50.dispense(17, transfer_plate[c])
        p50.drop_tip()

    # Move off magnet for mixing / bead resuspension
    protocol.move_labware(transfer_plate, MAG_STAGING, use_gripper=True)
    _plate_at[transfer_plate] = 'staging'

    for c in COLS:
        p50.pick_up_tip(p50_tips.get_tip())
        p50.mix(10, 15, transfer_plate[c].bottom(0.5))
        p50.blow_out(transfer_plate[c].top(-2))
        p50.drop_tip()

    if not DRYRUN:
        protocol.delay(minutes=2, msg="Elution incubation 2 min (NEB 2.3.13)")
    protocol.move_labware(transfer_plate, mag_block, use_gripper=True,
                          drop_offset={"x": 0, "y": 0, "z": -1})
    _plate_at[transfer_plate] = 'magnet'
    if not DRYRUN:
        protocol.delay(minutes=5, msg="Mag sep 5 min (NEB 2.3.14)")

    # ════════════════════════════════════════════════════════════════
    # PAUSE 2: Setup for PCR (~3-5 min)
    # ════════════════════════════════════════════════════════════════
    protocol.pause(
        "Size selection complete. Eluate ready on mag block.\n"
        "1. Remove ALL spent tip racks from deck and staging.\n"
        "2. Load 2x p50 racks (A3, C3).\n"
        "   Load 1x p50 reserve rack on A4 (staging).\n"
        "   (No 200µL racks needed — Phase 3 uses p50 only.)\n"
        "3. Place EMPTY PCR plate on the thermocycler (lid is open).\n"
        "4. Place NEBNext UDI primer plate on slot A2.\n"
        "   (A2 is free — transfer plate is on the mag block.)\n"
        "5. Press Resume.")
    reset_tips()

    # User placed pcr_plate on TC and primer_plate on A2
    protocol.move_labware(pcr_plate, thermocycler, use_gripper=False)
    _plate_at[pcr_plate] = 'thermocycler'
    protocol.move_labware(primer_plate, MAG_STAGING, use_gripper=False)

    # ================================================================
    # PHASE 3: PCR ENRICHMENT (NEB 2.4)
    # Tips needed: 4×N p50 + 0 p1000 (max 48 p50 at N=12)
    # p50: 36 avail per phase (auto-reload before Q5 MM if N≥10)
    # p1000: not used in Phase 3 (Q5 MM switched to p50 in V14)
    # ================================================================
    protocol.comment("══ PHASE 3: PCR ENRICHMENT ══")

    # ── 12. Transfer 15 uL eluate to PCR plate (NEB 2.4.1) ──
    # Two-step aspiration with reduced bottom rate — precious eluate
    tc_open()
    p50.configure_for_volume(15)
    for c in COLS:
        p50.pick_up_tip(p50_tips.get_tip())
        p50.aspirate(10, transfer_plate[c].bottom(1), rate=0.5)
        protocol.delay(seconds=5)
        p50.aspirate(5, transfer_plate[c].bottom(0.3), rate=0.2)
        p50.move_to(transfer_plate[c].top())
        p50.dispense(15, pcr_plate[c])
        p50.blow_out(pcr_plate[c].top(-2))
        p50.drop_tip()

    # Discard spent transfer plate
    protocol.move_labware(transfer_plate, waste_chute, use_gripper=True)
    _plate_at.pop(transfer_plate, None)

    # V14: Check p50 availability for Q5 + primers + PCR mix (3N cols needed).
    # After eluate transfer, we've used N p50. At N≥10, 36-N < 3N.
    p50_needed = 3 * NUM_COL
    p50_avail = _p50_cols_remaining()
    if p50_avail < p50_needed:
        protocol.pause(
            f"Need {p50_needed} p50 tip columns for Q5 MM + primers + PCR mix "
            f"but only {p50_avail} remain.\n"
            "1. Remove spent p50 racks from deck and staging.\n"
            "2. Load 2x fresh p50 racks (A3, C3).\n"
            "   Load 1x p50 reserve rack on A4 (staging).\n"
            "3. Press Resume.")
        p50_tips.reset()

    # ── 13. Q5 Master Mix: 25 uL (NEB 2.4.1 Option B) ──
    # V14: Q5 MM moved from reservoir to reagent plate cols 6-7.
    #       Switched from p1000 to p50 for accuracy at 28 µL (56% of p50 range).
    #       p1000+p200 tip was under-delivering at this volume.
    # Source-well mix volume is dynamic: 70% of remaining volume in that well,
    # capped at 40 µL (p50 tip safety) and floored at 17 µL.
    # At 8 samp (1 col): well has 25 µL → mix at 17 µL.
    # At 96 samp (12 cols): well starts at 150 µL → mix at 40 µL (capped).
    p50.configure_for_volume(25)
    for i, c in enumerate(COLS):
        q5_well = _q5_mm_well(i)
        p50.pick_up_tip(p50_tips.get_tip())
        if i % 4 == 0:
            q5_cols_used = i if i < 6 else i - 6
            q5_cols_this = min(NUM_COL, 6) if i < 6 else max(NUM_COL - 6, 0)
            q5_remaining = 25 * (q5_cols_this - q5_cols_used)
            q5_mix_vol = int(min(40, max(17, q5_remaining * 0.7)))
            p50.mix(3, q5_mix_vol, q5_well.bottom(0.5))
        p50.aspirate(25 + 3, q5_well.bottom(0.5), rate=0.5)
        p50.dispense(3, q5_well.bottom(1), rate=0.5)
        p50.move_to(q5_well.top(-2))        # gentle withdraw (replaces touch_tip)
        protocol.delay(seconds=3)            # let hanging drop fall back
        p50.dispense(25, pcr_plate[c], rate=0.5)
        p50.blow_out(pcr_plate[c].top(-2))
        p50.drop_tip()

    # ── 14. Index primers: 10 uL column-to-column (NEB 2.4.1 Option B) ──
    p50.configure_for_volume(10)
    for c in COLS:
        p50.pick_up_tip(p50_tips.get_tip())
        p50.aspirate(10 + 3, primer_plate[c])
        p50.dispense(3, primer_plate[c].bottom(1))
        protocol.delay(seconds=3)
        p50.dispense(10, pcr_plate[c])
        p50.drop_tip()

    # Discard primer plate to free A2
    protocol.move_labware(primer_plate, waste_chute, use_gripper=True)
    _plate_at.pop(primer_plate, None)

    # ── 15. Mix PCR reactions (NEB 2.4.2) ──
    for c in COLS:
        p50.pick_up_tip(p50_tips.get_tip())
        p50.mix(10, 40, pcr_plate[c].bottom(0.5))
        p50.blow_out(pcr_plate[c].top(-2))
        p50.drop_tip()

    # ── PCR thermocycling (NEB 2.4.3) ──
    tc_close()
    if not DRYRUN:
        thermocycler.set_lid_temperature(105)
        thermocycler.execute_profile(
            steps=[{'temperature': 98, 'hold_time_seconds': 30}],
            repetitions=1, block_max_volume=50)
        thermocycler.execute_profile(
            steps=[{'temperature': 98, 'hold_time_seconds': 10},
                   {'temperature': 65, 'hold_time_seconds': 75}],
            repetitions=PCR_CYCLES, block_max_volume=50)
        thermocycler.execute_profile(
            steps=[{'temperature': 65, 'hold_time_minutes': 5}],
            repetitions=1, block_max_volume=50)
        thermocycler.set_block_temperature(4)

    # ════════════════════════════════════════════════════════════════
    # PAUSE 3: Setup for post-PCR cleanup (~1-2 min)
    # ════════════════════════════════════════════════════════════════
    protocol.pause(
        "PCR complete. Libraries at 4C on thermocycler.\n"
        "1. Remove spent tip racks from deck and staging.\n"
        "2. Load 2x p50 racks (A3, C3) + 2x 200µL racks (B3, C2).\n"
        "   Load 1x p50 reserve rack on A4 (staging).\n"
        "   Load 1x 200µL reserve rack on B4 (staging).\n"
        "3. Place EMPTY output plate on staging slot A2.\n"
        "4. Press Resume.")
    reset_tips()

    # User placed output plate on A2
    protocol.move_labware(output_plate, MAG_STAGING, use_gripper=False)
    _plate_at[output_plate] = 'staging'

    # ================================================================
    # PHASE 4: POST-PCR CLEANUP (NEB 2.5)
    # Tips needed: 5×N p50 + (2N + 2N+2) p1000
    # At N=12: 60 p50 + 50 p1000
    # p50: 36 avail per phase (auto-reload before RSB; fires at N≥8)
    # p1000: 36 on-deck+reserve for beads/sup/wash1, then auto-reload
    #        inside etoh_wash() when tips run low.
    # V15: +N p50 from bead transfer switch to p50.
    # ================================================================
    protocol.comment("══ PHASE 4: POST-PCR CLEANUP ══")

    # output_plate is on A2 from user placement; pcr_plate is on TC from PCR.
    # Need a 3-way swap: A2 and TC are both occupied.
    # Use mag_block as temp parking for pcr_plate.
    tc_open()
    move_plate(pcr_plate, 'magnet')           # pcr TC → magnet (temp park)
    move_plate(output_plate, 'thermocycler')  # output A2 → TC (frees A2)
    move_plate(pcr_plate, 'staging')          # pcr magnet → A2

    # ── 16. AMPure beads: 45 uL = 0.9× of 50 uL PCR rxn (NEB 2.5.1) ──
    # V15: Bead transfer switched to p50 for accuracy at 48 µL (96% of range).
    # p1000 retained for reservoir pre-mix (150 µL) and well mixing (80 µL).
    # Same dual-pipette pattern as step 6 (first bead bind).
    p50.configure_for_volume(45)
    for c in COLS:
        p1000.pick_up_tip(p1000_tips.get_tip())
        p1000.mix(5, 150, BEADS)
        # p1000 holds tip while p50 does accurate bead transfer
        p50.pick_up_tip(p50_tips.get_tip())
        p50.aspirate(45 + 3, BEADS, rate=0.5)
        p50.dispense(3, BEADS.bottom(1), rate=0.5)
        protocol.delay(seconds=3)
        p50.dispense(45, pcr_plate[c])
        p50.drop_tip()
        # p1000 mixes beads into sample (80 µL exceeds p50 max)
        p1000.mix(10, 80, pcr_plate[c].bottom(0.5))
        p1000.blow_out(pcr_plate[c].top(-2))
        p1000.drop_tip()

    if not DRYRUN:
        protocol.delay(minutes=5, msg="Bead bind 5 min RT (NEB 2.5.2)")
    move_plate(pcr_plate, 'magnet')
    if not DRYRUN:
        protocol.delay(minutes=5, msg="Mag sep 5 min (NEB 2.5.3)")

    # ── 17. Remove supernatant ~95 uL ──
    # Two-pass pattern: bulk then sweep with over-aspiration.
    for c in COLS:
        p1000.pick_up_tip(p1000_tips.get_tip())
        # Pass 1: bulk
        p1000.move_to(pcr_plate[c].center())
        p1000.aspirate(95, pcr_plate[c].bottom(3), rate=0.25)
        p1000.move_to(pcr_plate[c].top())
        p1000.dispense(95, TRASH)
        p1000.blow_out(TRASH.top())
        # Pass 2: sweep (200 µL from z=1, gets residual + air)
        p1000.move_to(pcr_plate[c].center())
        p1000.aspirate(200, bead_loc(pcr_plate[c], 'sup'), rate=0.1)
        p1000.move_to(pcr_plate[c].top())
        p1000.dispense(200, TRASH)
        p1000.blow_out(TRASH.top())
        p1000.drop_tip()

    # ── 18. EtOH washes ×2 (NEB 2.5.5-2.5.6) ──
    etoh_wash(pcr_plate, COLS)

    # ── 19. Air dry (NEB 2.5.7) ──
    if not DRYRUN:
        protocol.delay(minutes=5, msg="Air dry beads up to 5 min (NEB 2.5.7)")

    # V14: Check p50 availability for RSB elution + final transfer (3N cols).
    p50_needed = 3 * NUM_COL
    p50_avail = _p50_cols_remaining()
    if p50_avail < p50_needed:
        protocol.pause(
            f"Need {p50_needed} p50 tip columns for RSB elution + final transfer "
            f"but only {p50_avail} remain.\n"
            "1. Remove spent p50 racks from deck and staging.\n"
            "2. Load 2x fresh p50 racks (A3, C3).\n"
            "   Load 1x p50 reserve rack on A4 (staging).\n"
            "3. Press Resume.")
        p50_tips.reset()

    # ── 20. RSB elution: 33 uL (NEB 2.5.8) ──
    # Add RSB to pellet on magnet first, then move off for resuspension
    # pcr_plate is already on magnet from EtOH washes
    move_plate(output_plate, 'thermocycler')

    p50.configure_for_volume(33)
    for c in COLS:
        p50.pick_up_tip(p50_tips.get_tip())
        p50.aspirate(33 + 3, RSB.bottom(1))
        p50.dispense(3, RSB.bottom(1))
        protocol.delay(seconds=3)
        p50.dispense(33, pcr_plate[c])
        p50.drop_tip()

    # Move off magnet for mixing / bead resuspension
    move_plate(pcr_plate, 'staging')

    for c in COLS:
        p50.pick_up_tip(p50_tips.get_tip())
        p50.mix(10, 25, pcr_plate[c].bottom(0.5))
        p50.blow_out(pcr_plate[c].top(-2))
        p50.drop_tip()

    if not DRYRUN:
        protocol.delay(minutes=2, msg="Elution 2 min (NEB 2.5.9)")
    move_plate(pcr_plate, 'magnet')
    if not DRYRUN:
        protocol.delay(minutes=5, msg="Mag sep 5 min (NEB 2.5.10)")

    # ── 21. Transfer 30 uL final library to output plate (NEB 2.5.10) ──
    # Two-step aspiration with reduced bottom rate — most precious transfer
    move_plate(output_plate, 'staging')
    for c in COLS:
        p50.pick_up_tip(p50_tips.get_tip())
        p50.aspirate(20, pcr_plate[c].bottom(1), rate=0.5)
        protocol.delay(seconds=5)
        p50.aspirate(10, pcr_plate[c].bottom(0.3), rate=0.2)
        p50.move_to(pcr_plate[c].top())
        p50.dispense(30, output_plate[c])
        p50.blow_out(output_plate[c].top(-2))
        p50.drop_tip()

    # ── CLEANUP ─────────────────────────────────────────────────────
    protocol.move_labware(pcr_plate, waste_chute, use_gripper=True)
    _plate_at.pop(pcr_plate, None)
    if not DRYRUN:
        temp_mod.deactivate()
        thermocycler.deactivate_block()
        thermocycler.deactivate_lid()

    protocol.comment("══ PROTOCOL COMPLETE ══")
    protocol.comment(
        f"Output plate with {protocol.params.NUM_SAMPLES} indexed libraries "
        f"is on staging slot A2.")
    protocol.comment("Store at -20C or proceed to QC (Qubit + BioAnalyzer/TapeStation).")
