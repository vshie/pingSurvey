/*
 * Interface Tour: the steps, in order.
 *
 * Kept apart from index.html so the wording can be edited without touching the app. Each step:
 *   section   heading shown on the card
 *   tab       0 Live Survey, 1 Post-Mission Processing, 2 Data Sharing, 3 Files (switched to first)
 *   target    [data-tour="..."] name (or a CSS selector starting with # or .) to spotlight; null = centred card
 *   title     card title
 *   text      HTML, or a function (t) => HTML for text that uses the live data
 *   before    async (t) => ...  sets up the interface for this step (the tour does it, not the student)
 *   tryIt     { text, done: (t) => bool }  the student does something; Next unlocks once done() is true
 *   placement 'right' | 'left' | 'bottom' | 'top' (default: wherever there is room)
 *
 * t is the tour helper object (see tourHelpers() in index.html): t.app is the page, t.examples the
 * example files, plus helpers to select logs, wait for the data-quality check, generate contours, etc.
 */

const TOUR_FOOTPRINT = 2 * Math.tan(12.5 * Math.PI / 180);   // footprint width = depth x this (25 degree beam)

function tourNum(n, digits) {
    return n === null || n === undefined || isNaN(n) ? '?' : Number(n).toLocaleString(undefined, { maximumFractionDigits: digits || 0 });
}

const PING_TOUR_STEPS = [
    // ------------------------------------------------------------------------------------------------
    {
        section: 'Welcome',
        target: null,
        title: 'Welcome to the Interface Tour!',
        text: `
            <p>This tour shows you everything this extension can do, using two real surveys collected by
            BlueBoat robotic boats. Along the way you'll learn how scientists and surveyors measure the shape
            of the sea floor: a science called <b>bathymetry</b> (from Greek <i>bathys</i>, "deep", and
            <i>metron</i>, "measure").</p>
            <p>Bathymetry maps help ships avoid running aground, show where fish and coral live, track how
            beaches and riverbeds change after storms, and help engineers plan piers, cables and wind farms.</p>
            <p>The tour takes about 15–20 minutes. Use <b>Next</b> and <b>Back</b> (or the ← → keys).
            When you see a <b>Try it</b> box, it's your turn to click something. <b>Exit tour</b> (or Esc)
            stops at any time, and everything you had set up before the tour is put back.</p>`
    },
    {
        section: 'Welcome',
        target: null,
        title: 'How does a boat measure depth?',
        text: `
            <p>The BlueBoat carries a <b>Ping sonar</b> pointing straight down. It sends out a short "ping" of
            sound and listens for the echo bouncing back off the bottom. Sound travels through water at about
            <b>1,500 metres per second</b>, so if the echo comes back after 0.02 seconds, the sound went down
            and back up in that time:</p>
            <p style="text-align:center"><b>depth = 1,500 m/s × 0.02 s ÷ 2 = 15 m</b></p>
            <p>At the same moment, the boat's <b>GPS</b> records exactly where it is. One depth plus one
            position is called a <b>sounding</b>. This extension records two soundings every second. Drive the
            boat back and forth in neat lines (like mowing a lawn) and you collect thousands of soundings that
            together reveal the shape of the bottom.</p>`
    },
    {
        section: 'Welcome',
        target: '.main-tabs',
        title: 'Four tabs, four jobs',
        text: `
            <ul>
                <li><b>Live Survey</b>: watch soundings arrive while the boat is out on the water, and start or stop recording.</li>
                <li><b>Post-Mission Processing</b>: back on shore, check the data for mistakes and turn it into a depth contour map.</li>
                <li><b>Data Sharing</b>: when several boats survey together, collect each other's data.</li>
                <li><b>Files</b>: see, download or delete the files stored on the boat.</li>
            </ul>
            <p>We'll visit them in that order.</p>`
    },

    // ------------------------------------------------------------------------------------------------
    {
        section: 'Live Survey',
        tab: 0,
        target: 'live-map',
        placement: 'bottom',
        title: 'The live map',
        before: async t => {
            await t.simulate(t.examples.small.name);
            await t.centerLive(19);   // the live map's closest zoom, so 4-5 m footprints are easy to see
        },
        text: t => `
            ${t.app.run ? `<p><i>Your boat is recording right now, so the tour is showing your live data instead of a replay.</i></p>` : `
            <p>We've started a <b>replay</b> of a real survey, sped up 5×, so you can see what the screen looks
            like while a boat is working. This one was collected in a harbour near Los Angeles, California.</p>`}
            <p>The marker is the boat. Each circle is one sounding. The circle is drawn the size of the patch of
            sea floor the sonar "sees": its beam spreads out in a 25° cone, so the deeper the water, the bigger
            the patch (called the <b>footprint</b>). At 10 m deep the footprint is about 4.4 m across; at 40 m
            it's about 18 m.</p>
            <p>Circles are only drawn for soundings the sonar is confident about. You can drag the map and
            zoom with the mouse wheel or the + and − buttons.</p>`
    },
    {
        section: 'Live Survey',
        tab: 0,
        target: 'live-legend',
        title: 'Reading the colours',
        text: `
            <p>The circles are coloured by depth: <b>pale for shallow water, dark blue for deep water</b>, just
            like real nautical charts. Click the legend to fold it away.</p>
            <p>Watch the colours as the replay runs: if the circles slowly darken, the boat is heading into
            deeper water. If colours change suddenly from one circle to the next, the bottom may be steep, or
            one of the readings may be wrong (more on that later).</p>
            <p>When boats share data (the Data Sharing tab), their soundings appear here too, each outlined in
            its own colour.</p>`
    },
    {
        section: 'Live Survey',
        tab: 0,
        target: 'live-console',
        placement: 'top',
        title: 'The Status Console',
        text: `
            <p>This table shows the newest sounding, updated about twice a second:</p>
            <ul>
                <li><b>Latitude / Longitude</b>: where the boat is, in degrees north/south of the equator and east/west of Greenwich, England.</li>
                <li><b>Depth</b>: how far the sonar measured down to the bottom, in metres.</li>
                <li><b>Beam Coverage Diameter</b>: how wide the footprint is at this depth.</li>
                <li><b>Confidence</b>: how sure the sonar is (next step).</li>
                <li><b>Yaw / Roll / Pitch</b>: the boat's heading, side-to-side tilt and front-to-back tilt. A tilted boat points the sonar slightly sideways, so big waves make soundings less accurate.</li>
                <li><b>Altitude</b>: the GPS antenna's height. Ordinary GPS altitude can be off by several metres; special "RTK" GPS is accurate to a few centimetres.</li>
            </ul>`
    },
    {
        section: 'Live Survey',
        tab: 0,
        target: 'live-console',
        placement: 'top',
        title: 'What does "confidence" mean?',
        text: `
            <p>Echoes aren't always clean. A school of fish, bubbles from the boat's wake, soft mud or very deep
            water can all give the sonar a weak or confusing echo. So for every reading the Ping sonar also
            reports a <b>confidence</b> from 0 to 100%: how sure it is that the echo really came from the
            bottom.</p>
            <p>Green numbers mean the sonar is confident; orange and red mean it's unsure. Later, when we
            process the data, we'll throw away soundings below a confidence threshold so they don't spoil the
            map.</p>
            <p>If you see low confidence for a long time, slow the boat down, avoid sharp turns, and check that
            the sonar is clean and fully underwater.</p>`
    },
    {
        section: 'Live Survey',
        tab: 0,
        target: 'live-record',
        placement: 'top',
        title: 'Recording a survey',
        text: `
            <ul>
                <li><b>Start / Stop</b> begins and ends recording. Each recording becomes one <b>log file</b> on the boat itself, named with the date and time it started (for example <code>ping_survey_2026-10-01_15-25-29.csv</code>). Saving on the boat means a weak radio link can't lose any data. Even if you lose sight of the boat, the survey is safe.</li>
                <li><b>Download</b> copies the log being recorded to your computer.</li>
                <li><b>Simulate</b> replays a saved survey, like the replay you're watching now. It's handy for practising without a boat.</li>
            </ul>
            <p>(The tour won't press Start, so it never creates a real log.)</p>`
    },
    {
        section: 'Live Survey',
        tab: 0,
        target: 'live-map',
        placement: 'bottom',
        title: 'If the sonar goes quiet',
        text: `
            <p>If you're recording but no depth readings are arriving, a <b>red warning banner</b> appears at
            the top of this map. The most common cause is that the boat's computer didn't find the sonar when
            it switched on, for example if the sonar's cable was loose or it powered up late.</p>
            <p>The banner has a <b>Re-detect Ping</b> button that asks the boat to search for the sonar again
            without restarting anything. The extension also tries this once by itself a few seconds after
            recording starts.</p>`
    },
    {
        section: 'Live Survey',
        tab: 0,
        target: 'btn-clear',
        placement: 'top',
        title: 'Center and Clear',
        text: `
            <p><b>Center</b> jumps the map back to the boat. It only works once the GPS has a position fix,
            which can take a minute after the boat switches on.</p>
            <p><b>Clear</b> wipes the circles off this map. It only tidies the screen: the recorded log file
            is not touched.</p>`
    },
    {
        section: 'Live Survey',
        tab: 0,
        target: 'live-mapcontrols',
        placement: 'top',
        title: 'Maps without internet',
        text: `
            <p>The satellite background comes from the internet, but out on the water there usually isn't any.
            So before a trip, while you still have internet, save ("cache") the map tiles for your survey area
            on the boat:</p>
            <ul>
                <li><b>Cache View</b> saves everything currently on screen, down to very fine detail.</li>
                <li><b>Cache Region</b> lets you draw an outline around a bigger area to save.</li>
                <li><b>Recent Area</b> jumps to the last area you saved.</li>
                <li><b>Refresh</b> updates how much map is stored, and <b>Clear Cache</b> deletes it all to free space.</li>
            </ul>`
    },
    {
        section: 'Live Survey',
        tab: 0,
        target: 'live-map',
        placement: 'bottom',
        title: 'Try it: measure your line spacing',
        before: async t => { t.measureOff(); },
        text: t => {
            const live = t.app.tableItems[0];
            const d = live && Number(live.confidence) >= 90 ? Number(live.depth) : 12;   // the harbour is about 12 m deep
            return `
            <p>Surveyors drive in parallel lines. If the lines are too far apart, the footprints don't touch and
            parts of the bottom are never measured. If they're very close, you waste time measuring the same
            spot twice.</p>
            <p>Right now the water is about <b>${tourNum(d, 1)} m</b> deep, so each footprint is about
            <b>${tourNum(d * TOUR_FOOTPRINT, 1)} m</b> wide. For full coverage, the lines should be no further
            apart than that.</p>`;
        },
        tryIt: {
            text: 'Click the <b>ruler</b> button (under the zoom buttons), then click two points on the map, for example on two neighbouring lines of circles. The distance appears on the line. A third click starts a new measurement.',
            done: t => t.measurePoints(t.app.measure) >= 2
        }
    },
    {
        section: 'Live Survey',
        tab: 0,
        target: 'widget-url',
        placement: 'top',
        title: 'Using the extension in Cockpit',
        before: async t => { t.measureOff(); },
        text: `
            <p><b>Cockpit</b> is Blue Robotics' app for driving the boat. You can add this extension to Cockpit
            as a small <b>widget</b> showing depth, confidence and a mini-map while you drive: copy this address
            and paste it into a Cockpit iframe widget.</p>
            <p>Cockpit's own maps can also show the soundings while you record, and the finished contour map
            afterwards, so the driver can see what has been covered.</p>`
    },

    // ------------------------------------------------------------------------------------------------
    {
        section: 'Post-Mission: small survey',
        tab: 1,
        target: '.main-tabs',
        title: 'From soundings to a map',
        before: async t => { await t.stopSimulation(); },
        text: `
            <p>Back on shore, we turn thousands of soundings into a <b>contour map</b>. A contour line joins
            places that are the same depth, just like the lines on a hiking map join places of the same height.
            Where lines crowd together the bottom is steep; where they're far apart it's flat.</p>
            <p>The panel on the left goes through five steps, from top to bottom:</p>
            <ol>
                <li>Choose the survey logs.</li>
                <li>Check the data quality.</li>
                <li>Choose the area to map.</li>
                <li>Choose how the contours look.</li>
                <li>Generate the map.</li>
            </ol>`
    },
    {
        section: 'Post-Mission: small survey',
        tab: 1,
        target: 'pm-file-list',
        placement: 'right',
        title: 'Try it: choose a survey log',
        before: async t => { await t.select([]); },
        text: t => `
            <p>Every recording made on this boat is listed here, newest first. The tour has added two example
            surveys: <b>${t.examples.small.name}</b> (small, ${tourNum(t.examples.small.rows)} soundings) and
            <b>${t.examples.large.name}</b> (large, ${tourNum(t.examples.large.rows)} soundings). They stay
            here after the tour so you can practise with them.</p>
            <p>The download icon next to a log saves the raw file, a CSV spreadsheet you can also open in Excel
            or Google Sheets.</p>`,
        tryIt: {
            text: t => `Tick <b>${t.examples.small.name}</b> to load it.`,
            done: t => t.app.pm.selectedFiles.includes(t.examples.small.name) && t.app.pm.pointsTotal > 0
        }
    },
    {
        section: 'Post-Mission: small survey',
        tab: 1,
        target: 'pm-map',
        title: 'Every sounding on the map',
        before: async t => {
            await t.select([t.examples.small.name]);
        },
        text: `
            <p>Each ring is one sounding's footprint, coloured by depth: <b>yellow is shallow, purple is
            deep</b>. You can see the boat's survey lines and where they overlap.</p>
            <p>Small grey dots are soundings the sonar wasn't confident about. They'll be left out of the map.</p>
            <p>To keep the map quick to draw, only some soundings along each line are shown. All of them are
            still used to make the map.</p>`
    },
    {
        section: 'Post-Mission: small survey',
        tab: 1,
        target: 'pm-minconf',
        placement: 'right',
        title: 'The confidence filter',
        text: t => `
            <p>Only soundings at or above this confidence are used. The default, <b>95%</b>, keeps only readings
            the sonar was very sure about.</p>
            <p>For this survey, <b>${tourNum(t.app.pmAcceptedCount)} of ${tourNum(t.app.pm.pointsTotal)}</b>
            soundings pass. That's very clean data. Lowering the slider keeps more soundings but lets in more
            bad ones; raising it does the opposite. Scientists always have to balance <b>quantity</b> against
            <b>quality</b>.</p>`
    },
    {
        section: 'Post-Mission: small survey',
        tab: 1,
        target: 'pm-tides',
        placement: 'right',
        title: 'Tides: the water level moves!',
        text: `
            <p>The sonar measures from the <b>water surface</b>, but the surface rises and falls with the tide,
            by more than 2 m a day in some places. The same rock could read 10 m deep in the morning and 12 m in
            the afternoon!</p>
            <p>So charts measure depth from a fixed reference level called a <b>datum</b>. Common ones are
            <b>LAT</b> (Lowest Astronomical Tide), <b>MLLW</b> (Mean Lower Low Water, used on US charts) and
            <b>MSL</b> (Mean Sea Level). We subtract the tide height at the moment each sounding was taken.</p>
            <ul>
                <li><b>Online tide service</b> looks up tide heights from the nearest tide station: free from NOAA in the US, or WorldTides worldwide with a free key.</li>
                <li><b>Manual</b> lets you type heights from a printed tide table.</li>
            </ul>
            <p>The tour uses manual heights of 0, so our maps show depth below the water surface at survey time.</p>`
    },
    {
        section: 'Post-Mission: small survey',
        tab: 1,
        target: 'pm-quality',
        placement: 'right',
        title: '2. Data quality',
        before: async t => { await t.qc(); },
        text: t => {
            const qc = t.app.pm.qc;
            const flags = qc ? (qc.conflict_count + qc.spike_count) : 0;
            return `
            <p>Next the extension checks the soundings for problems, in two ways:</p>
            <ul>
                <li><b>Overlaps</b>: where two survey lines cross, both should measure the same depth. If they disagree a lot, something is wrong.</li>
                <li><b>Spikes</b>: a single reading that jumps far away from its neighbours along the same line.</li>
            </ul>
            <p>${flags ? `This small survey has only <b>${tourNum(qc.conflict_count)}</b> overlap disagreement(s) and
            <b>${tourNum(qc.spike_count)}</b> spike(s), which is pretty clean. We'll fix problems properly with
            the large survey later.` : `This small survey passed every check, so we can move on. We'll see what
            problems look like with the large survey later.`}</p>`;
        }
    },
    {
        section: 'Post-Mission: small survey',
        tab: 1,
        target: 'pm-area',
        placement: 'right',
        title: '3. Processing area',
        before: async t => { t.fitArea(); },
        text: `
            <p>The orange outline marks the area we'll make a map of. The tour pressed <b>Fit to data</b>, which
            draws an outline just around the soundings.</p>
            <p>Why limit the area? Between survey lines, the computer fills in depths by connecting nearby
            soundings, which is fair because they're close together. Outside the surveyed area it would be
            <b>guessing</b>, and a map should never show guesses as facts.</p>
            <p>You can also draw an outline yourself with <b>Draw area</b>, for example to leave out a messy
            corner, and <b>Undo</b> or <b>Clear</b> it.</p>`
    },
    {
        section: 'Post-Mission: small survey',
        tab: 1,
        target: 'pm-map',
        title: 'Try it: edit the outline',
        before: async t => {
            if (t.app.pm.polygon.length < 4) t.fitArea();
            t.app.pmLeaflet.map.fitBounds(t.app.pm.polygon, { padding: [40, 40] });
            t.mark.vertices = t.app.pm.polygon.length;
        },
        text: `
            <p>The orange dots are the outline's corners, called <b>vertices</b>. Drag a vertex to move it.
            Click a vertex to select it; a small window lets you delete it. You can also right-click it, or
            select it and press the Delete key.</p>`,
        tryIt: {
            text: 'Click any orange vertex, then press <b>Delete vertex</b>.',
            done: t => t.app.pm.polygon.length < t.mark.vertices
        }
    },
    {
        section: 'Post-Mission: small survey',
        tab: 1,
        target: 'pm-autotune',
        placement: 'right',
        title: '4. Contours: auto-tune',
        before: async t => { await t.qc(); },
        text: t => {
            const notes = (t.app.pm.qc && t.app.pm.qc.tuned && t.app.pm.qc.tuned.notes) || [];
            return `
            <p>With <b>auto-tune</b> on, the extension looks at the soundings and chooses sensible settings
            itself. Here's what it decided for this survey:</p>
            <ul>${notes.map(n => `<li>${n}</li>`).join('')}</ul>
            <p>Change any setting yourself and auto-tune switches off; switch it back on to recalculate.</p>`;
        }
    },
    {
        section: 'Post-Mission: small survey',
        tab: 1,
        target: 'pm-vref',
        placement: 'right',
        title: 'Vertical reference',
        text: `
            <p>How do we know the height of the water surface? Two ways:</p>
            <ul>
                <li><b>Depth + tide</b> (the usual way): measured depth minus the tide height, as we just saw.</li>
                <li><b>GPS altitude (RTK)</b>: an RTK GPS knows its own height to within a few centimetres. Knowing how high the antenna sits above the sonar, the extension can work out the bottom's height directly, with no tide table needed. It's only available when the log recorded altitude and the boat has an RTK GPS.</li>
            </ul>`
    },
    {
        section: 'Post-Mission: small survey',
        tab: 1,
        target: 'pm-intervals',
        placement: 'right',
        title: 'Contour intervals and style',
        text: t => `
            <p>The <b>minor interval</b> is how often a contour line is drawn
            (here every <b>${t.app.pm.settings.minor_m} m</b>). Every so often a thicker, labelled <b>major</b>
            line is drawn (here every <b>${t.app.pm.settings.major_m} m</b>) so you can read depths at a glance,
            like the darker "index contours" on hiking maps.</p>
            <p>Smaller intervals show more detail, but only if the data is good enough. Lines closer together
            than the sonar's accuracy would just be drawing noise.</p>
            <p>Below, you can choose each line type's colour, thickness and see-through-ness (opacity), and
            the labels' size, colour and spacing.</p>`
    },
    {
        section: 'Post-Mission: small survey',
        tab: 1,
        target: 'pm-advanced',
        placement: 'right',
        title: 'Advanced settings',
        text: `
            <p>Under <b>Advanced</b> (click to open) you'll find:</p>
            <ul>
                <li><b>Surface smoothing</b>: evens out small bumps so contour lines aren't jagged.</li>
                <li><b>Max mesh triangle edge</b>: gaps between soundings wider than this are left blank instead of filled in.</li>
                <li><b>GeoTIFF pixel size</b>: how much ground each pixel of the finished image covers.</li>
                <li>The <b>data-quality tolerances</b>: how big a disagreement has to be before it's flagged.</li>
            </ul>
            <p><b>Reset to defaults</b> puts everything back.</p>`
    },
    {
        section: 'Post-Mission: small survey',
        tab: 1,
        target: 'pm-generate',
        placement: 'right',
        scroll: 'start',
        title: 'Try it: generate the map',
        text: `
            <p>Pressing <b>Generate contours</b> makes the boat's own computer (a Raspberry Pi) do the work:</p>
            <ol>
                <li>It joins neighbouring soundings into a mesh of triangles, called a <b>TIN</b> (Triangulated Irregular Network).</li>
                <li>It reads depths off the triangles onto an even grid, like graph paper.</li>
                <li>It smooths the grid and traces the contour lines through it.</li>
                <li>It draws the lines and labels onto an image that knows exactly where on Earth it belongs: a <b>GeoTIFF</b>.</li>
            </ol>
            <p>The bar shows each stage as it runs.</p>`,
        tryIt: {
            text: 'Press <b>Generate contours</b> and watch the progress bar.',
            done: t => !t.app.pm.processing && t.resultFor([t.examples.small.name])
        }
    },
    {
        section: 'Post-Mission: small survey',
        tab: 1,
        target: 'pm-map',
        title: 'Reading the contour map',
        before: async t => {
            await t.generate([t.examples.small.name]);
            t.showSoundings(false);
            t.app.pm.showContours = true;
            t.app.pmShowOverlay(t.app.pm.result);
            t.app.pmLeaflet.map.fitBounds(t.app.pm.result.bounds, { padding: [20, 20] });
        },
        text: t => `
            <p>Here's the finished map: yellow minor lines, green labelled major lines, with depths in metres.
            This patch of sea floor goes from <b>${t.app.pm.result.depth_min_m} m</b> to
            <b>${t.app.pm.result.depth_max_m} m</b> deep.</p>
            <p>Look at the spacing: where lines bunch up the bottom slopes steeply; where they spread out it's
            gentle.</p>
            <p>The tour hid the sounding rings so the lines are easy to see; <b>Show soundings on map</b> in step 1
            brings them back. The button at the top right hides or shows the contours, so you can compare them
            with the soundings underneath.</p>`
    },
    {
        section: 'Post-Mission: small survey',
        tab: 1,
        target: 'pm-result',
        placement: 'right',
        scroll: 'start',
        title: 'The result',
        text: `
            <p>The summary tells you how many contour lines were drawn, the depth range, how many soundings
            were used, and the image size.</p>
            <p><b>EPSG</b> is a code for the map projection. The Earth is round but images are flat, so the
            image uses a <b>UTM</b> zone, a flat grid in metres that fits one slice of the globe.</p>
            <p><b>Download GeoTIFF</b> saves the map. Load it into Cockpit as a map layer, or into mapping
            software like QGIS or Google Earth Pro. It's transparent apart from the lines, so it sits neatly on
            top of satellite images.</p>`
    },

    // ------------------------------------------------------------------------------------------------
    {
        section: 'Post-Mission: large survey',
        tab: 1,
        target: 'pm-map',
        title: 'A bigger, messier survey',
        before: async t => {
            await t.select([t.examples.large.name]);
            await t.qc();
        },
        text: t => `
            <p>Now for the large example: <b>${tourNum(t.app.pm.pointsTotal)}</b> soundings collected off
            Kailua-Kona, Hawaii. The boat started near a shallow reef and went out over a steep drop-off to
            nearly 80 m deep.</p>
            <p>Real surveys are rarely perfect, and this one shows the kinds of problems you'll meet. Notice
            all the grey: lots of soundings had low confidence.</p>`
    },
    {
        section: 'Post-Mission: large survey',
        tab: 1,
        target: 'pm-counts',
        placement: 'right',
        title: 'Many more rejected soundings',
        text: t => `
            <p><b>${tourNum(t.app.pmAcceptedCount)} of ${tourNum(t.app.pm.pointsTotal)}</b> soundings pass the
            95% confidence filter, so <b>${tourNum(t.app.pm.pointsTotal - t.app.pmAcceptedCount)}</b> were
            rejected: far more than in the small survey. Why?</p>
            <ul>
                <li><b>Deep water</b>: the echo from 70–80 m is much fainter.</li>
                <li><b>Steep slopes</b>: the beam hits shallow and deep parts at once, so the echo gets smeared.</li>
                <li><b>Turning and starting up</b>: bubbles under the hull and a tilted sonar confuse the echo.</li>
            </ul>
            <p>Rejecting them is easy. But some bad soundings still have high confidence, so we need the
            data-quality checks to catch those.</p>`
    },
    {
        section: 'Post-Mission: large survey',
        tab: 1,
        target: 'pm-quality',
        placement: 'right',
        title: 'Problems found',
        text: t => {
            const qc = t.app.pm.qc || {};
            return `
            <p>The checks found <b>${tourNum(qc.conflict_count)} overlap disagreements</b> and
            <b>${tourNum(qc.spike_count)} spikes</b>. That's typical for a survey over steep, rocky ground.</p>
            <p>Each problem is listed with its most serious examples first, and marked on the map with a ring:
            <span style="color:#ff1744">red</span> for overlap disagreements,
            <span style="color:#ff9100">orange</span> for spikes. Let's look at each kind.</p>`;
        }
    },
    {
        section: 'Post-Mission: large survey',
        tab: 1,
        target: 'pm-conflicts',
        placement: 'right',
        title: 'Overlap disagreements',
        text: t => {
            const c = t.app.pmConflictsShown[0];
            return `
            <p>Where two survey lines pass over the same spot, they should measure the same depth. A
            <b>disagreement</b> is a pair of soundings, from different passes, that are close together but
            differ by more than the allowed amount.</p>
            <p>Each row shows how far apart the two soundings were, how much their depths differ, and the
            <b>slope</b> that would explain it (a 100% slope rises 1 m for every 1 m across, which is very
            steep).</p>
            ${c ? `<p>The worst one here: two soundings <b>${c.h_m} m</b> apart that differ by <b>${c.dz_m} m</b>.
            At this depth the footprint is about ${c.a.footprint_m} m wide, so on a steep slope the two pings
            may simply have "seen" different parts of the bottom. Not every disagreement is an error!</p>` : ''}`;
        }
    },
    {
        section: 'Post-Mission: large survey',
        tab: 1,
        target: 'pm-map',
        title: 'Comparing two soundings',
        before: async t => {
            const c = t.app.pmConflictsShown[0];
            if (!c) return;
            t.app.pmZoomTo(c.a);
            await t.wait(600);
            t.openFlag(c.a);
        },
        text: `
            <p>The tour zoomed to the worst disagreement and opened its details. The red line joins
            <b>Sounding A</b> and <b>Sounding B</b>; the window shows each one's depth, confidence and time.</p>
            <p>If one is clearly wrong (for example it doesn't match the soundings around it), press
            <b>Exclude</b> for that one and it's left out of the map. If both look believable (a steep slope),
            press <b>Dismiss</b> to say "I've checked this, it's fine". Clicking a row in the list zooms the
            map to that pair.</p>`
    },
    {
        section: 'Post-Mission: large survey',
        tab: 1,
        target: 'pm-conflicts',
        placement: 'right',
        title: 'Try it: dismiss a disagreement',
        before: async t => { t.closePopups(); },
        text: `
            <p>Each row has an <b>×</b> on the right. It dismisses that row: the next one moves up and the
            count goes down by one. Dismissing doesn't delete any data; it just records that you've reviewed it.
            <b>Dismiss all</b> does the whole group at once.</p>`,
        tryIt: {
            text: 'Click the <b>×</b> on one of the rows.',
            done: t => t.app.pm.ignored.some(id => id.startsWith('conflict:'))
        }
    },
    {
        section: 'Post-Mission: large survey',
        tab: 1,
        target: 'pm-spikes',
        placement: 'right',
        title: 'Spikes',
        text: t => {
            const s = t.app.pmSpikesShown[0];
            return `
            <p>A <b>spike</b> is a single sounding that jumps away from its neighbours along the same line: a
            fish swimming under the boat, a cloud of bubbles, the boat's own wake, or an echo that bounced
            twice (called <b>multipath</b>).</p>
            <p>To find them, the extension looks at the soundings just before and after each one and works out
            their middle value (the <b>median</b>). If a sounding is much further from that median than its
            neighbours normally vary, it's a spike. On a rough reef the neighbours vary a lot, so it's more
            forgiving there.</p>
            ${s ? `<p>The biggest here reads <b>${s.depth_m} m</b> while its neighbours read about
            <b>${s.neighbour_median_m} m</b>: a jump of ${Math.abs(s.deviation_m)} m in half a second!</p>` : ''}`;
        }
    },
    {
        section: 'Post-Mission: large survey',
        tab: 1,
        target: 'pm-map',
        title: 'Excluding one sounding',
        before: async t => {
            const s = t.app.pmSpikesShown[0];
            if (!s) return;
            t.app.pmZoomTo(s);
            await t.wait(600);
            t.openFlag(s);
            await t.wait(900);
            t.app.pmExclude(s);
            t.mark.excluded = s;
        },
        text: `
            <p>Here's the biggest spike, ringed in orange. The tour pressed <b>Exclude this sounding</b> in its
            window, so it's now shown as a dashed grey ring and won't be used for the map.</p>
            <p>Excluding is how you remove individual bad readings by hand.</p>`
    },
    {
        section: 'Post-Mission: large survey',
        tab: 1,
        target: 'pm-excluded',
        placement: 'right',
        title: 'Excluded soundings',
        text: `
            <p>Everything you exclude is listed here, so nothing disappears without a trace. Click an entry to
            zoom to it, or press <b>Restore</b> to put it back. <b>Restore all</b> undoes every exclusion.</p>
            <p>Keeping a record of every change you make to data is an important habit in science: someone
            else should be able to see exactly what you did.</p>`
    },
    {
        section: 'Post-Mission: large survey',
        tab: 1,
        target: 'pm-spikes',
        placement: 'right',
        title: 'Exclude all spikes at once',
        before: async t => {
            t.closePopups();
            t.app.pm.settings.exclude_spikes = true;
        },
        text: `
            <p>Going through spikes one by one takes a while, so the tour pressed <b>Exclude all spikes</b>:
            every detected spike is left out of the map. The ✓ shows they're handled, and <b>Keep spikes in the
            mesh</b> would undo it.</p>
            <p>If a "spike" is actually real, like a tall rock, dismiss it with its <b>×</b> and it stays in the
            map even with this switched on.</p>`
    },
    {
        section: 'Post-Mission: large survey',
        tab: 1,
        target: 'pm-dismissed',
        placement: 'right',
        title: 'Dismissed items',
        text: `
            <p>Whatever you dismiss is collected here, grouped by type. Close a chip with its × to bring those
            items back into the lists.</p>
            <p>The exclusions and dismissals are also saved with the finished map, so you can always tell
            how it was made.</p>`
    },
    {
        section: 'Post-Mission: large survey',
        tab: 1,
        target: 'pm-quality',
        placement: 'right',
        title: 'Other warnings you may see',
        text: `
            <p>Two other checks only appear in particular situations:</p>
            <ul>
                <li><b>Logs disagree</b>: when you combine two surveys of the same place from different days, and one reads deeper everywhere they overlap, there's probably a tide difference that wasn't corrected. You can apply the difference as a tide correction, or remove one survey's overlapping soundings.</li>
                <li><b>GPS altitude isn't RTK quality</b>: when using the GPS-altitude method, if the altitude wobbles too much the contours won't be trustworthy.</li>
            </ul>
            <p>A hint also appears when raising the confidence filter would remove the flagged soundings
            automatically.</p>`
    },
    {
        section: 'Post-Mission: large survey',
        tab: 1,
        target: 'pm-generate',
        placement: 'right',
        scroll: 'start',
        title: 'Generate the large survey',
        before: async t => {
            t.fitArea();
            await t.generate([t.examples.large.name]);
        },
        text: t => `
            <p>The tour fitted the area to the data and generated the map, with your exclusions and with the
            spikes left out. Auto-tune chose <b>${t.app.pm.result.settings.minor_m} m</b> minor and
            <b>${t.app.pm.result.settings.major_m} m</b> major lines this time, because the depth range is much
            bigger than in the small survey.</p>
            <p>It used <b>${tourNum(t.app.pm.result.points_used)}</b> soundings to draw
            <b>${t.app.pm.result.major_lines + t.app.pm.result.minor_lines}</b> contour lines.</p>`
    },
    {
        section: 'Post-Mission: large survey',
        tab: 1,
        target: 'pm-map',
        title: 'The drop-off',
        before: async t => {
            t.showSoundings(false);
            t.app.pm.showContours = true;
            t.app.pmShowOverlay(t.app.pm.result);
            t.app.pmLeaflet.map.fitBounds(t.app.pm.result.bounds, { padding: [20, 20] });
        },
        text: `
            <p>See how the contour lines crowd together in one band? That's the steep drop-off where the reef
            ends and the bottom plunges towards 80 m. On the shallow reef the lines are far apart.</p>
            <p>Gaps in the lines are places where survey lines were too far apart, or every sounding was
            rejected. The map leaves them blank rather than guessing. The <b>ruler</b> under the zoom buttons
            works on this map too.</p>`
    },

    // ------------------------------------------------------------------------------------------------
    {
        section: 'Data Sharing',
        tab: 2,
        target: 'ds-vehicles',
        placement: 'right',
        title: 'Working as a fleet',
        text: `
            <p>Big areas are often surveyed by several boats at once, each covering its own lines. This tab
            finds the other boats on the same network that run this extension. It searches every address from
            192.168.2.1 to 192.168.2.254, which takes a few seconds.</p>
            <p>Each boat needs its own <b>vehicle ID</b> (its MAVLink system ID). Boats sharing this boat's ID
            are hidden, because their data couldn't be told apart.</p>
            <p>The <b>Sync</b> switch receives a boat's survey as it's recorded. Its soundings appear on your
            Live Survey map in that boat's colour, so crews can see who has covered what.</p>`
    },
    {
        section: 'Data Sharing',
        tab: 2,
        target: 'ds-logs',
        placement: 'left',
        title: "Copying another boat's logs",
        text: `
            <p>Select a boat to see its survey logs. Tick logs and press <b>Copy</b> to bring them onto this
            boat. Each copy gets the other boat's name added to the end
            (<code>ping_survey_…__boat2.csv</code>), so you always know where it came from.</p>
            <p>Copied and synced logs appear in Post-Mission Processing like any other, so you can combine the
            whole fleet's work into one map.</p>`
    },

    // ------------------------------------------------------------------------------------------------
    {
        section: 'Files',
        tab: 3,
        target: 'files-frame',
        placement: 'bottom',
        title: 'Files on the boat',
        text: `
            <p>This is the boat's file browser, opened at the folder where this extension keeps everything:</p>
            <ul>
                <li><code>ping_survey_….csv</code>: your recorded surveys, plus the two example surveys.</li>
                <li><code>processed</code>: your latest contour map (GeoTIFF, preview image and settings).</li>
                <li><code>processed_tour</code>: the maps this tour made, kept separate from yours.</li>
                <li><code>offline_maps</code>: saved satellite map tiles. <code>tides</code>: saved tide data.</li>
            </ul>
            <p>You can upload, download, rename and delete files here. Delete old surveys you no longer need, to
            keep space free on the boat.</p>`
    },

    // ------------------------------------------------------------------------------------------------
    {
        section: 'Finish',
        target: null,
        title: "You've finished the tour!",
        text: `
            <p>You've followed the whole journey of a bathymetric survey:</p>
            <ol>
                <li><b>Collect</b>: drive survey lines while the boat records soundings.</li>
                <li><b>Check</b>: filter out low-confidence readings, and review overlaps and spikes.</li>
                <li><b>Correct</b>: account for the tide so depths are measured from a fixed datum.</li>
                <li><b>Map</b>: build a triangle mesh and trace contour lines.</li>
                <li><b>Share</b>: download the GeoTIFF or pull in other boats' data.</li>
            </ol>
            <p>When you press <b>Finish</b>, everything you had set up before the tour is put back. The example
            surveys stay in the log list so you can practise. Run the tour again any time with the
            <b>Interface Tour</b> button at the top.</p>`
    }
];
