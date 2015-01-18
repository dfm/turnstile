#!/usr/bin/env python
# -*- coding: utf-8 -*-

from __future__ import division, print_function

import os
import sys
import glob
import json
import turnstile

import gc
import time
import cPickle
import traceback
from IPython.parallel import Client, require


@require(os, sys, gc, cPickle, traceback, time)
def search(bp):
    # Insane hackish output capturing context.
    # http://stackoverflow.com/questions/16571150
    #   /how-to-capture-stdout-output-from-a-python-function-call
    class Capturing(object):

        def __init__(self, fn):
            self.fn = fn

        def __enter__(self):
            self._stdout = sys.stdout
            sys.stdout = self._fh = open(self.fn, "a")
            return self

        def __exit__(self, *args):
            self._fh.close()
            sys.stdout = self._stdout

    # Execute the pipeline.
    r, q, pipe = None, None, None
    try:
        with open(os.path.join(bp, "pipeline.pkl"), "rb") as f:
            q, pipe = cPickle.load(f)

        strt = time.time()
        with Capturing(os.path.join(bp, "output.log")):
            r = pipe.query(**q)

        with open(os.path.join(bp, "output.log"), "a") as f:
            f.write("Finished in {0} seconds\n".format(time.time() - strt))

    except:
        with open(os.path.join(bp, "error.log"), "a") as f:
            f.write("Error during execution:\n\n")
            f.write(traceback.format_exc())

    finally:
        # Try to fix memory leaks.
        del r
        del q
        del pipe
        gc.collect()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()

    parser.add_argument("file_glob", help="pattern for the LC files")
    parser.add_argument("basis_file", help="the archive of PCA comps")
    parser.add_argument("base_dir", help="the directory for output")
    parser.add_argument("njobs", type=int, help="the number of jobs to run")
    parser.add_argument("--min-period", type=float, default=5.0,
                        help="minimum period")
    parser.add_argument("--max-period", type=float, default=80.0,
                        help="maximum period")
    parser.add_argument("-p", "--profile-dir", default=None,
                        help="the IPython profile dir")

    args = parser.parse_args()
    print("Running with the following arguments:")
    print("sys.argv:")
    print(sys.argv)
    print("args:")
    print(args)

    # Build the pipeline first.
    pipe = turnstile.K2Data(cache=False)
    oned = pipe = turnstile.OneDSearch(pipe)
    pipe = turnstile.TwoDSearch(pipe, cache=False)
    pipe = turnstile.PeakDetect(pipe, cache=False)
    pipe = turnstile.FeatureExtract(pipe, cache=False)
    pipe = turnstile.Validate(pipe, cache=False)

    query = dict(
        basis_file=os.path.abspath(args.basis_file),
        durations=[0.1, 0.2],
        min_period=args.min_period,
        max_period=args.max_period,
        validation_path=os.path.join(args.base_dir),
    )

    # Initialize the pool.
    c = Client(profile_dir=args.profile_dir)
    pool = c.load_balanced_view()
    jobs = []

    # Loop over the files.
    for fn in glob.iglob(args.file_glob):
        epicid = os.path.split(fn)[-1].split("-")[0][4:]
        outdir = os.path.abspath(os.path.join(args.base_dir, epicid))
        if os.path.exists(os.path.join(outdir, "results", "features.h5")):
            print("skipping {0}".format(epicid))
            continue

        # Update the query.
        query["kicid"] = "EPIC {0}".format(epicid)
        query["light_curve_file"] = os.path.abspath(fn)
        query["validation_path"] = os.path.join(outdir, "results")
        oned.basepath = os.path.join(outdir, "cache")

        # Save the files.
        try:
            os.makedirs(outdir)
        except os.error:
            pass
        with open(os.path.join(outdir, "pipeline.pkl"), "w") as f:
            cPickle.dump((query, pipe), f, -1)
        with open(os.path.join(outdir, "query.json"), "w") as f:
            json.dump(query, f, sort_keys=True, indent=4)

        # Submit the job.
        jobs.append((outdir, pool.apply(search, outdir)))

    # Monitor the jobs and check for completion and errors.
    retrieved = [False] * len(jobs)
    while not all(retrieved):
        for i, (fn, j) in enumerate(jobs):
            if j.ready() and not retrieved[i]:
                try:
                    j.get()
                except Exception as e:
                    with open(os.path.join(fn, "error.log"), "a") as f:
                        f.write("Uncaught error:\n\n")
                        f.write(traceback.format_exc())
                else:
                    with open(os.path.join(fn, "success.log"), "w") as f:
                        f.write("Finished at: {0}\n".format(time.time()))
                retrieved[i] = True
        time.sleep(1)