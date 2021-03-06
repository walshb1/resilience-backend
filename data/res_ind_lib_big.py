#!/usr/bin/python3
import logging
import numpy as np
import pandas as pd


#help with multiindex dataframe
#from pandas_helper import get_list_of_index_names, broadcast_simple, concat_categories

from scipy.interpolate import interp1d

logging.basicConfig(
    filename='model.log', level=logging.DEBUG,
    format='%(asctime)s: %(levelname)s: %(message)s')


#import pandas as pd

# from sorted_nicely import sorted_nicely


def get_list_of_index_names(df):
    """returns name of index in a data frame as a list. (single element list if the dataframe as a single index)""
    """

    if df.index.name is None:
        return list(df.index.names)
    else:
        return [df.index.name] #do not use list( ) as list breaks strings into list of chars



def broadcast_simple( df_in, index):
    """simply replicates df n times and adds index (where index has n distinct elements) as the last level of a multi index.
    if index is a multiindex with (m,p) this will work too (and replicte df n=m *p times). But if some of the levels of index are already included in df_in (BASED ON NAME ONLY), these are ignored (see example).

    EXAMPLES

    s=pd.DataFrame(["a","b","c"], index=pd.Index(["A", "B", "C"], name="letters"), columns=["value"])
    s

        value
    A 	a
    B 	b

    #works
    my_index=pd.Index(["one", "two"], name="numbers")
    broadcast_simple(s, my_index)

       numbers
    A  one        a
       two        a
    B  one        b
       two        b
   Name: value, dtype: object

    #multi index example
    my_index=pd.MultiIndex.from_product([["one", "two"], ["cat", "dog"]], names=["numbers", "pets"])
    broadcast_simple(s, my_index)

       numbers  pets
    A  one      cat     a
                dog     a
       two      cat     a
                dog     a
    B  one      cat     b
                dog     b
       two      cat     b
                dog     b
    Name: value, dtype: object

    #Ignored level in multi index example
    my_index=pd.MultiIndex.from_product([["one", "two"], ["X", "Y"]], names=["numbers", "letters"])
    broadcast_simple(s, my_index)

    letters  numbers
    A        one        a
             two        a
    B        one        b
             two        b
    C        one        c
             two        c


    #Raise error because the index should be named
    my_index=pd.Index(["one", "two"])
    broadcast_simple(s, my_index)

    """

    #in case of MultiIndex, performs this function on each one of the levels of the index
    if type(index)== pd.MultiIndex:
        y = df_in.copy()
        for idxname in [i for i in index.names if i not in get_list_of_index_names(df_in)]:
                y = broadcast_simple(y, index.get_level_values(idxname))
        return y

    cat_list = index.unique()
    nb_cats =len(cat_list)
    if index.name is None:
        raise Exception("index should be named")


    y= pd.concat([df_in]*nb_cats,
                    keys = cat_list,
                    names=[index.name]+get_list_of_index_names(df_in)
                 )

    #puts new index at the end
    y=y.reset_index(index.name).set_index(index.name, append=True).sort_index()

    return y.squeeze()


def concat_categories(p,np, index):
    """works like pd.concat with keys but swaps the index so that the new index is innermost instead of outermost
    http://pandas.pydata.org/pandas-docs/stable/merging.html#concatenating-objects
    """

    if index.name is None:
        raise Exception("index should be named")


    y= pd.concat([p, np],
        keys = index,
        names=[index.name]+get_list_of_index_names(p)
            )#.sort_index()

    #puts new index at the end
    y=y.reset_index(index.name).set_index(index.name, append=True).sort_index()

    #makes sure a series is returned when possible
    return y.squeeze()


#name of admin division
economy = "name"
#levels of index at which one event happens
event_level = [economy, "hazard", "rp"]

#return period to use when no rp is provided (mind that this works with protection)
default_rp = "default_rp"

#categories of households
income_cats   = pd.Index(["poor","nonpoor"],name="income_cat")
#categories for social protection
affected_cats = pd.Index(["a", "na"]            ,name="affected_cat")
helped_cats   = pd.Index(["helped","not_helped"],name="helped_cat")


def compute_resilience(df_in,cat_info, hazard_ratios=None, is_local_welfare=True, return_iah=False, return_stats=False,optionT="data", optionPDS="unif_poor", optionB = "data", loss_measure = "dk",fraction_inside=1, verbose_replace=False, optionFee="tax",  share_insured=.25):
    """Main function. Computes all outputs (dK, resilience, dC, etc,.) from inputs
    optionT=="perfect","data","x33","incl" or "excl"
    optionPDS=="no","unif_all","unif_poor","prop"
    optionB=="data","unif_poor"
    optionFee == "tax" (default) or "insurance_premium"
    fraction_inside=0..1 (how much aid is paid domestically)
    """

    #make sure to copy inputs
    macro    =    df_in.dropna().copy(deep=True)
    cat_info = cat_info.dropna().copy(deep=True)


    ####DEFAULT VALUES
    if type(hazard_ratios)==pd.DataFrame:

        #make sure to copy inputs
        hazard_ratios = hazard_ratios.dropna().copy(deep=True)

        #other way of passing dummy hazard ratios
        if hazard_ratios.empty:
            hazard_ratios=None

    #default hazard
    if hazard_ratios is None:
        hazard_ratios = pd.Series(1,index=pd.MultiIndex.from_product([macro.index,"default_hazard"],names=[economy, "hazard"]))

    #if fa ratios were provided with no hazard data, they are broadcasted to default hazard
    if "hazard" not in get_list_of_index_names(hazard_ratios):
        hazard_ratios = broadcast_simple(hazard_ratios, pd.Index(["default_hazard"], name="hazard"))

    #if hazard data has no rp, it is broadcasted to default hazard
    if "rp" not in get_list_of_index_names(hazard_ratios):
        hazard_ratios_event = broadcast_simple(hazard_ratios, pd.Index([default_rp], name="rp"))
    else:
        #interpolates data to a more granular grid for return periods that includes all protection values
        hazard_ratios_event = interpolate_rps(hazard_ratios,macro.protection)  #XXX: could move this after dkdw into average over rp (but parallel computing within pandas probably means no difference)


    #########
    ## PRE PROCESS and harmonize input values
    #removes countries in macro not in cat_info, otherwise it crashes
    common_places = [c for c in macro.index if c in cat_info.index and c in hazard_ratios.index]
    macro = macro.ix[common_places]
    cat_info = cat_info.ix[common_places]
    hazard_ratios = hazard_ratios.ix[common_places]


    ##consistency of income, gdp, etc.
    # gdp from k and mu
    macro["gdp_pc_pp"]= macro["avg_prod_k"]*agg_to_economy_level(cat_info,"k")

    # conso from k and macro
    cat_info["c"]=(1-macro["tau_tax"])*macro["avg_prod_k"]*cat_info["k"]+ cat_info["gamma_SP"]*macro["tau_tax"]*macro["avg_prod_k"]*agg_to_economy_level(cat_info,"k")

    #add finance to diversification and taxation
    cat_info["social"] = unpack_social(macro,cat_info)
    cat_info["social"]+= 0.1* cat_info["axfin"]
    macro["tau_tax"], cat_info["gamma_SP"] = social_to_tx_and_gsp(cat_info)

    #RECompute consumption from k and new gamma_SP and tau_tax
    cat_info["c"]=(1-macro["tau_tax"])*macro["avg_prod_k"]*cat_info["k"]+ cat_info["gamma_SP"]*macro["tau_tax"]*macro["avg_prod_k"]*agg_to_economy_level(cat_info,"k")


    # # # # # # # # # # # # # # # # # # #
    # MACRO_MULTIPLIER
    # # # # # # # # # # # # # # # # # # #

    #rebuilding exponentially to 95% of initial stock in reconst_duration
    three = np.log(1/0.05)
    recons_rate = three/ macro["T_rebuild_K"]

    # Calculation of macroeconomic resilience
    macro["macro_multiplier"] =(macro["avg_prod_k"] +recons_rate)/(macro["rho"]+recons_rate)

    ####FORMATING
    #gets the event level index
    event_level_index = hazard_ratios_event.reset_index().set_index(event_level).index

    #Broadcast macro to event level
    macro_event = broadcast_simple(macro,  event_level_index)
    #updates columns in macro with columns in hazard_ratios_event
    cols = [c for c in macro_event if c in hazard_ratios_event]
    if not cols==[]:
        macro_event[cols] =  hazard_ratios_event[cols]
    if verbose_replace:
        print("Replaced in macro: "+", ".join(cols))

    #Broadcast categories to event level
    cats_event = broadcast_simple(cat_info,  event_level_index)
    # applies mh ratios to relevant columns
    cols_c = [c for c in cats_event if c in hazard_ratios_event] #columns that are both in cats_event and hazard_ratios_event


    if not cols_c==[]:
        hrb = broadcast_simple( hazard_ratios_event[cols_c], cat_info.index).reset_index().set_index(get_list_of_index_names(cats_event)) #explicitly broadcasts hazard ratios to contain income categories
        cats_event[cols_c] = hrb
        if verbose_replace:
            print("Replaced in cats: "+", ".join(cols_c))
    if verbose_replace:
        print("Replaced in both: "+", ".join(np.intersect1d(cols,cols_c)))

    ####COMPUTING LOSSES
    #computes dk and dW per event
    out=compute_dK_dW(macro_event, cats_event, optionT=optionT, optionPDS=optionPDS, optionB=optionB, return_iah=return_iah,  return_stats= return_stats,is_local_welfare=is_local_welfare, loss_measure=loss_measure,fraction_inside=fraction_inside, optionFee=optionFee,  share_insured=share_insured)

    #unpacks if needed
    if return_iah:
        dkdw_event,cats_event_iah  = out
    else:
        dkdw_event = out

    ##AGGREGATES LOSSES
    #Averages over return periods to get dk_{hazard} and dW_{hazard}
    dkdw_h = average_over_rp(dkdw_event,macro_event["protection"])

    #Sums over hazard dk, dW (gets one line per economy)
    dkdw = dkdw_h.sum(level=economy)

    #adds dk and dw-like columns to macro
    macro[dkdw.columns]=dkdw

    #computes socio economic capacity and risk at economy level
    macro = calc_risk_and_resilience_from_k_w(macro, is_local_welfare)

    ###OUTPUTS
    if return_iah:
        return macro, cats_event_iah
    else:
        return macro


def compute_dK_dW(macro_event, cats_event, optionT="data", optionPDS="unif_poor", optionB="data", optionFee="tax", return_iah=False, return_stats=False, is_local_welfare=True,loss_measure="dk",fraction_inside=1, share_insured=.25):
    '''Computes dk and dW line by line.
    presence of multiple return period or multihazard data is transparent to this function'''


    ################## MICRO
    ####################
    #### Consumption losses per AFFECTED CATEGORIES before response
    cats_event_ia=concat_categories(cats_event,cats_event, index= affected_cats)
    #counts affected and non affected
    naf = cats_event["n"]*cats_event.fa
    nna = cats_event["n"]*(1-cats_event.fa)
    cats_event_ia["n"] = concat_categories(naf,nna, index= affected_cats)

    #de_index so can access cats as columns and index is still event
    cats_event_ia = cats_event_ia.reset_index(["income_cat", "affected_cat"]).sort_index()

    #post early-warning vulnerability
    cats_event_ia["v_shew"]=cats_event_ia["v"]*(1-macro_event["pi"]*cats_event_ia["shew"])

    #capital losses and total capital losses (mind correcting unaffected dk to zero)
    cats_event_ia["dk"]  = cats_event_ia[["k","v_shew"]].prod(axis=1, skipna=False)
    #sets unaffected dk to 0
    cats_event_ia.ix[(cats_event_ia.affected_cat=='na') ,"dk" ]=0

    #"national" losses (to scale down transfers)
    macro_event["dk_event"] =  agg_to_event_level(cats_event_ia, "dk")

    #immediate consumption losses: direct capital losses plus losses through event-scale depression of transfers
    cats_event_ia["dc"] = (1-macro_event["tau_tax"])*cats_event_ia["dk"]  +  cats_event_ia["gamma_SP"]*macro_event["tau_tax"] *macro_event["dk_event"]

    # NPV consumption losses accounting for reconstruction and productivity of capital (pre-response)
    cats_event_ia["dc_npv_pre"] = cats_event_ia["dc"]*macro_event["macro_multiplier"]


    #POST DISASTER RESPONSE

    #baseline case (no insurance)
    if optionFee!="insurance_premium":
        macro_event, cats_event_iah = compute_response(macro_event, cats_event_ia,  optionT=optionT, optionPDS=optionPDS, optionB=optionB, optionFee=optionFee, fraction_inside=fraction_inside, loss_measure = loss_measure)

    #special case of insurance that adds to existing default PDS
    else:
        #compute post disaster response with default PDS from data ONLY
        m__,c__ = compute_response(macro_event, cats_event_ia,optionT="data", optionPDS="unif_poor", optionB="data", optionFee="tax", fraction_inside=1, loss_measure="dk")

        #compute post disaster response with insurance ONLY
        macro_event, cats_event_iah = compute_response(macro_event.assign(shareable=share_insured), cats_event_ia,  optionT=optionT, optionPDS=optionPDS, optionB=optionB, optionFee=optionFee, fraction_inside=fraction_inside, loss_measure = loss_measure)

        columns_to_add = ["need","aid"]
        macro_event[columns_to_add] +=  m__[columns_to_add]

        columns_to_add_iah = ["help_received","help_fee"]
        cats_event_iah[columns_to_add_iah] += c__[columns_to_add_iah]



    #effect on welfare
    cats_event_iah["dc_npv_post"] = cats_event_iah["dc_npv_pre"] -  cats_event_iah["help_received"]  + cats_event_iah["help_fee"]


    # print(cats_event_iah.head())
    # print("\n macro \n")
    # print(macro_event.head())


    cats_event_iah["dw"] = calc_delta_welfare(cats_event_iah, macro_event)

    #aggregates dK and delta_W at df level
    dK      = agg_to_event_level(cats_event_iah,"dk")
    delta_W = agg_to_event_level(cats_event_iah,"dw")


    ###########
    #OUTPUT
    df_out = pd.DataFrame(index=macro_event.index)

    # df_out["macro_multiplier"] = macro_event["macro_multiplier"]

    df_out["dK"] = dK
    df_out["dKtot"]=dK*macro_event["pop"] #/macro_event["protection"]

    df_out["delta_W"]    =delta_W
    df_out["delta_W_tot"]=delta_W*macro_event["pop"] #/macro_event["protection"]

    df_out["average_aid_cost_pc"] = macro_event["aid"]

    if return_stats:
        stats = np.setdiff1d(cats_event_iah.columns,event_level+['helped_cat',  'affected_cat',     'income_cat'])
        df_stats = agg_to_event_level(cats_event_iah, stats)
        # if verbose_replace:
        print("stats are "+",".join(stats))
        df_out[df_stats.columns]=(df_stats.T*macro_event.protection).T #corrects stats from protecgion because they get averaged over rp with the rest of df_out later

    if return_iah:
        return df_out,cats_event_iah
    else:
        return df_out


def compute_response(macro_event, cats_event_ia,  optionT="data", optionPDS="unif_poor", optionB="data", optionFee="tax", fraction_inside=1, loss_measure="dk"):
    """
    Computes aid received,  aid fee, and other stuff, from losses and PDS options on targeting, financing, and dimensioning of the help.
    Returns copies of macro_event and cats_event_iah updated with stuff
    TODO In general this function is ill coded and should be rewritteN
    """


    macro_event    = macro_event.copy()
    cats_event_ia = cats_event_ia.copy()

    macro_event["fa"] =  agg_to_event_level(cats_event_ia,"fa")


    #adding hELPED/NOT HELPED CATEGORIES, indexed at event level
    # !!!!!!!MIND THAT N IS 2 AT THIS LEVEL !!!!!!!!!!!!!!
    cats_event_iah = concat_categories(cats_event_ia,cats_event_ia, index= helped_cats).reset_index(helped_cats.name).sort_index()

    ####targeting errors
    if optionT=="perfect":
        macro_event["error_incl"] = 0
        macro_event["error_excl"] = 0
    elif optionT=="data":
        macro_event["error_incl"]=(1-macro_event["prepare_scaleup"])/2*macro_event["fa"]/(1-macro_event["fa"])
        macro_event["error_excl"]=(1-macro_event["prepare_scaleup"])/2
    elif optionT=="x33":
        macro_event["error_incl"]= .33*macro_event["fa"]/(1-macro_event["fa"])
        macro_event["error_excl"]= .33
    elif optionT=="incl":
        macro_event["error_incl"]= .33*macro_event["fa"]/(1-macro_event["fa"])
        macro_event["error_excl"]= 0
    elif optionT=="excl":
        macro_event["error_incl"]= 0
        macro_event["error_excl"]= 0.33
    else:
        print("unrecognized targeting error option")
        return None

    #counting (mind self multiplication of n)
    cats_event_iah.ix[(cats_event_iah.helped_cat=='helped')    & (cats_event_iah.affected_cat=='a') ,"n"]*=(1-macro_event["error_excl"])
    cats_event_iah.ix[(cats_event_iah.helped_cat=='not_helped')& (cats_event_iah.affected_cat=='a') ,"n"]*=(  macro_event["error_excl"])
    cats_event_iah.ix[(cats_event_iah.helped_cat=='helped')    & (cats_event_iah.affected_cat=='na'),"n"]*=(  macro_event["error_incl"])
    cats_event_iah.ix[(cats_event_iah.helped_cat=='not_helped')& (cats_event_iah.affected_cat=='na'),"n"]*=(1-macro_event["error_incl"])
    ###!!!! n is one again from here.

    # #should be only ones
    # cats_event_iah.n.sum(level=event_level)

    # MAXIMUM NATIONAL SPENDING ON SCALE UP
    macro_event["max_aid"] = macro_event["max_increased_spending"]*macro_event["borrow_abi"]*macro_event["gdp_pc_pp"]

    ##THIS LOOP DETERMINES help_received and help_fee by category   (currently may also output cats_event_ia[["need","aid","unif_aid"]] which might not be necessary )
    # how much post-disaster support?

    if optionB=="unif_poor":
        ### CALCULATE FIRST THE BUDGET FOR unif_poor and use the same budget for other methods
        d = cats_event_iah.ix[(cats_event_iah.affected_cat=='a') & (cats_event_iah.income_cat=='poor')]
        macro_event["need"] = macro_event["shareable"]*agg_to_event_level(d,loss_measure)
        macro_event["aid"] = macro_event["need"].clip(upper=macro_event["max_aid"])
    elif optionB=="one_per_affected":
        ### CALCULATE FIRST THE BUDGET FOR unif_poor and use the same budget for other methods
        d = cats_event_iah.ix[(cats_event_iah.affected_cat=='a')]
        d["un"]=1
        macro_event["need"] = agg_to_event_level(d,"un")
        macro_event["aid"] = macro_event["need"]
    elif optionB=="one":
        macro_event["aid"] = 1
    elif optionB=="x10":
        macro_event["aid"] = 0.1*macro_event["gdp_pc_pp"]
    elif optionB=="x05":
        macro_event["aid"] = 0.05*macro_event["gdp_pc_pp"]
    elif optionB=="max01":
        macro_event["max_aid"] = 0.01*macro_event["gdp_pc_pp"]
    elif optionB=="max05":
        macro_event["max_aid"]=0.05*macro_event["gdp_pc_pp"]
    elif optionB=="unlimited":
        d = cats_event_iah.ix[(cats_event_iah.affected_cat=='a')]
        macro_event["need"] = macro_event["shareable"]*agg_to_event_level(d,loss_measure)
        macro_event["aid"] = macro_event["need"]

    if optionFee == "tax":
        pass
    elif optionFee == "insurance_premium":
        pass

    if optionPDS=="no":
        macro_event["aid"] = 0
        cats_event_iah["help_received"] = 0
        cats_event_iah["help_fee"] =0

    elif optionPDS in ["unif_all", "unif_poor"]:

        if optionPDS=="unif_all":
            #individual need: NPV losses for affected
            d = cats_event_iah.ix[(cats_event_iah.affected_cat=='a')]
        elif optionPDS=="unif_poor":
            #NPV losses for POOR affected
            d = cats_event_iah.ix[(cats_event_iah.affected_cat=='a') & (cats_event_iah.income_cat=='poor')]

        #aggs need of those selected in the previous block (eg only poor) at event level
        macro_event["need"] = macro_event["shareable"]*agg_to_event_level(d,loss_measure)

        #actual aid reduced by capacity
        if optionB=="data":
            macro_event["aid"] = (macro_event["need"]*macro_event["prepare_scaleup"]*macro_event["borrow_abi"]).clip(upper=macro_event["max_aid"])
        elif optionB in ["max01" , "max05"]:
            macro_event["aid"] = (macro_event["need"]).clip(upper=macro_event["max_aid"])
            # otherwise we keep the aid from the unif_poor calculation (or one)

        #aid divided by people aided
        macro_event["unif_aid"] = macro_event["aid"]/(cats_event_iah.ix[cats_event_iah.helped_cat=="helped","n"].sum(level=event_level))

        #help_received: all who receive receive same
        cats_event_iah["help_received"] = 0
        cats_event_iah.ix[(cats_event_iah.helped_cat=='helped'),"help_received"]= macro_event["unif_aid"]

        #aid funding
        cats_event_iah["help_fee"] = fraction_inside*macro_event["aid"]*cats_event_iah["k"]/agg_to_event_level(cats_event_iah,"k")

    # $1 per helped person
    elif optionPDS=="one":
        macro_event["unif_aid"] = 1
        #help_received: all who receive receive same
        cats_event_iah["help_received"] = 0
        cats_event_iah.ix[(cats_event_iah.helped_cat=='helped'),"help_received"]= macro_event["unif_aid"]
        macro_event["need"] = agg_to_event_level(cats_event_iah,"help_received")
        macro_event["aid"] = macro_event["need"]
        cats_event_iah["help_fee"] = fraction_inside*macro_event["aid"]*cats_event_iah["k"]/agg_to_event_level(cats_event_iah,"k")

    elif optionPDS=="hundred":
        macro_event["unif_aid"] = macro_event["gdp_pc_pp"]
        #help_received: all who receive receive same
        cats_event_iah["help_received"] = 0
        cats_event_iah.ix[(cats_event_iah.helped_cat=='helped'),"help_received"]= macro_event["unif_aid"]
        macro_event["need"] = agg_to_event_level(cats_event_iah,"help_received")
        macro_event["aid"] = macro_event["need"]
        cats_event_iah["help_fee"] = fraction_inside*macro_event["aid"]*cats_event_iah["k"]/agg_to_event_level(cats_event_iah,"k")

    elif optionPDS in ["prop","perfect", "prop_nonpoor"]:

        #needs based on losses per income category (needs>0 for non affected people)
        cats_event_iah["need"] = 0
        cats_event_iah.ix[(cats_event_iah.income_cat=='poor'), "need"]   =0 if optionPDS=="prop_nonpoor" else cats_event_iah.ix[(cats_event_iah.income_cat=='poor')   & (cats_event_iah.affected_cat=='a') ,loss_measure].sum(level=event_level)
        cats_event_iah.ix[(cats_event_iah.income_cat=='nonpoor'),"need"] =cats_event_iah.ix[(cats_event_iah.income_cat=='nonpoor')& (cats_event_iah.affected_cat=='a') ,loss_measure].sum(level=event_level)

        d = cats_event_iah.ix[cats_event_iah.helped_cat=="helped",["need","n"]]
            # "national" needs: agg over helped people
        macro_event["need"] = macro_event["shareable"]*agg_to_event_level(d,"need")

        # actual aid is national need reduced by capacity
        if optionB=="data":
            macro_event["aid"] = (macro_event["need"]*macro_event["prepare_scaleup"]*macro_event["borrow_abi"]).clip(upper=macro_event["max_aid"])
        elif optionB in ["max01" , "max05"]:
            macro_event["aid"] = (macro_event["need"]).clip(upper=macro_event["max_aid"])

        #actual individual aid reduced prorate by capacity (mind fixing to zero when not helped)
        cats_event_iah["help_received"] = macro_event["shareable"]*cats_event_iah["need"]*  (macro_event["aid"]/macro_event["need"])  #individual (line in cats_event_iah) need scaled by "national" (cats_event_ia line)
        cats_event_iah.ix[(cats_event_iah.helped_cat=='not_helped'),"help_received"]=0

        # financed at prorata of individual assets over "national" assets

        if optionFee=="tax":
            cats_event_iah["help_fee"] = fraction_inside * agg_to_event_level(cats_event_iah,"help_received")*cats_event_iah["k"]/agg_to_event_level(cats_event_iah,"k")

        elif optionFee=="insurance_premium":

            cats_event_iah.ix[(cats_event_iah.income_cat=='poor'),"help_fee"] = fraction_inside * agg_to_event_level(cats_event_iah.query("income_cat=='poor'"),"help_received")

            cats_event_iah.ix[(cats_event_iah.income_cat=='nonpoor'),"help_fee"] = fraction_inside * agg_to_event_level(cats_event_iah.query("income_cat=='nonpoor'"),"help_received")

        else:
            print("did not know how to finance the PDS")

    else:
        print("unrecognised optionPDS treated as no")


    return macro_event, cats_event_iah






def calc_risk_and_resilience_from_k_w(df, is_local_welfare):
    """Computes risk and resilience from dk, dw and protection. Line by line: multiple return periods or hazard is transparent to this function"""

    df=df.copy()

    ############################
    #Expressing welfare losses in currency

    #discount rate
    rho = df["rho"]
    h=1e-4

    #Reference losses
    h=1e-4

    if is_local_welfare:
        wprime =(welf(df["gdp_pc_pp"]/rho+h,df["income_elast"])-welf(df["gdp_pc_pp"]/rho-h,df["income_elast"]))/(2*h)
        # wprime =(welf(df["gdp_pc_pp_ref"]/rho+h,df["income_elast"])-welf(df["gdp_pc_pp_ref"]/rho-h,df["income_elast"]))/(2*h)
    else:
        wprime =(welf(df["gdp_pc_pp_nat"]/rho+h,df["income_elast"])-welf(df["gdp_pc_pp_nat"]/rho-h,df["income_elast"]))/(2*h)

    dWref   = wprime*df["dK"]

    #expected welfare loss (per family and total)
    df["dWpc_currency"] = df["delta_W"]/wprime  #//df["protection"]
    df["dWtot_currency"]=df["dWpc_currency"]*df["pop"];

    #welfare loss (per family and total)
    #df["dWpc_currency"] = df["delta_W"]/wprime/df["protection"]
    #df["dWtot_currency"]=df["dWpc_currency"]*df["pop"];

    #Risk to welfare as percentage of local GDP
    df["risk"]= df["dWpc_currency"]/(df["gdp_pc_pp"]);

    ############
    #SOCIO-ECONOMIC CAPACITY)
    df["resilience"] =dWref/(df["delta_W"] );

    ############
    #RISK TO ASSETS
    df["risk_to_assets"]  =df.resilience* df.risk;

    return df



def calc_delta_welfare(micro, macro):
    """welfare cost from consumption before (c)
    an after (dc_npv_post) event. Line by line"""




    #computes welfare losses per category
    dw = welf(micro["c"]                       /macro["rho"], macro["income_elast"]) -\
         welf(micro["c"]/macro["rho"]-(micro["dc_npv_post"]), macro["income_elast"])

    return dw


def welf(c,elast):
    """"Welfare function"""

    y=(c**(1-elast)-1)/(1-elast)

    #log welfare func
    # cond = (elast==1)
    # y[cond] = np.log(c[cond])

    return y

def agg_to_event_level (df, seriesname):
    """ aggregates seriesname in df (string of list of string) to event level (country, hazard, rp) using n in df as weight
    does NOT normalize weights to 1."""
    return (df[seriesname].T*df["n"]).T.sum(level=event_level)

def agg_to_economy_level (df, seriesname):
    """ aggregates seriesname in df (string of list of string) to economy (country) level using n in df as weight
    does NOT normalize weights to 1."""
    return (df[seriesname].T*df["n"]).T.sum(level=economy)


def interpolate_rps(fa_ratios,protection_list):

    ###INPUT CHECKING
    if fa_ratios is None:
        return None

    if default_rp in fa_ratios.index:
        return fa_ratios

    flag_stack= False
    if "rp" in get_list_of_index_names(fa_ratios):
        fa_ratios = fa_ratios.unstack("rp")
        flag_stack = True

    if type(protection_list) in [pd.Series, pd.DataFrame]:
        protection_list=protection_list.squeeze().unique().tolist()

    #in case of a Multicolumn dataframe, perform this function on each one of the higher level columns
    if type(fa_ratios.columns)==pd.MultiIndex:
        keys = fa_ratios.columns.get_level_values(0).unique()
        return pd.concat({col:interpolate_rps(fa_ratios[col],protection_list) for col in  keys}, axis=1).stack("rp")


    ### ACTAL FUNCTION
    #figures out all the return periods to be included
    all_rps = list(set(protection_list+fa_ratios.columns.tolist()))

    fa_ratios_rps = fa_ratios.copy()

    #extrapolates linear towards the 0 return period exposure  (this creates negative exposure that is tackled after interp) (mind the 0 rp when computing probas)
    if len(fa_ratios_rps.columns)==1:
        fa_ratios_rps[0] = fa_ratios_rps.squeeze()
    else:
        fa_ratios_rps[0]=fa_ratios_rps.iloc[:,0]- fa_ratios_rps.columns[0]*(
        fa_ratios_rps.iloc[:,1]-fa_ratios_rps.iloc[:,0])/(
        fa_ratios_rps.columns[1]-fa_ratios_rps.columns[0])


    #add new, interpolated values for fa_ratios, assuming constant exposure on the right
    x = fa_ratios_rps.columns.values
    y = fa_ratios_rps.values
    fa_ratios_rps= pd.concat(
        [pd.DataFrame(interp1d(x,y,bounds_error=False)(all_rps),index=fa_ratios_rps.index, columns=all_rps)]
        ,axis=1).sort_index(axis=1).clip(lower=0).fillna(method="pad",axis=1)
    fa_ratios_rps.columns.name="rp"

    if flag_stack:
        fa_ratios_rps = fa_ratios_rps.stack("rp")

    return fa_ratios_rps


def average_over_rp(df,protection=None):
    """Aggregation of the outputs over return periods"""

    if protection is None:
        protection=pd.Series(0,index=df.index)

    #does nothing if df does not contain data on return periods
    try:
        if "rp" not in df.index.names:
            print("rp was not in df")
            return df
    except(TypeError):
        pass

    #just drops rp index if df contains default_rp
    if default_rp in df.index.get_level_values("rp"):
        # print("default_rp detected, droping rp")
        return (df.T/protection).T.reset_index("rp",drop=True)


    df=df.copy().reset_index("rp")
    protection=protection.copy().reset_index("rp",drop=True)

    #computes probability of each return period
    return_periods=np.unique(df["rp"].dropna())

    proba = pd.Series(np.diff(np.append(1/return_periods,0)[::-1])[::-1],index=return_periods) #removes 0 from the rps

    #matches return periods and their probability
    proba_serie=df["rp"].replace(proba)

    #removes events below the protection level
    proba_serie[protection>df.rp] =0

    #handles cases with multi index and single index (works around pandas limitation)
    idxlevels = list(range(df.index.nlevels))
    if idxlevels==[0]:
        idxlevels =0

    #average weighted by proba
    averaged = df.mul(proba_serie,axis=0).sum(level=idxlevels) # obsolete .div(proba_serie.sum(level=idxlevels),axis=0)

    return averaged.drop("rp",axis=1)



def unpack_social(m,cat):
        """Compute social from gamma_SP, taux tax and k and avg_prod_k
        """
        #############
        #### preparation

        #current conso and share of average transfer
        c  = cat.c
        gs = cat.gamma_SP

        #social_p
        social = gs* m.gdp_pc_pp  *m.tau_tax /c

        return social

def social_to_tx_and_gsp(cat_info):
        """(tx_tax, gamma_SP) from cat_info[["social","c","n"]] """

        tx_tax = cat_info[["social","c","n"]].prod(axis=1, skipna=False).sum(level=economy) / \
                 cat_info[         ["c","n"]].prod(axis=1, skipna=False).sum(level=economy)

        #income from social protection PER PERSON as fraction of PER CAPITA social protection
        gsp=     cat_info[["social","c"]].prod(axis=1,skipna=False) /\
             cat_info[["social","c","n"]].prod(axis=1, skipna=False).sum(level=economy)

        return tx_tax, gsp



def unpack(v,pv,fa,pe,ph,share1):
#returns v_p,v_r, far, fap, cp, cr from the inputs
# v_p,v_r, far, fap, cp, cr = unpack(v,pv,fa,pe,ph,share1)

    v_p = v*(1+pv)

    fap_ref= fa*(1+pe)


    far_ref=(fa-ph*fap_ref)/(1-ph)
    cp_ref=   share1 /ph
    cr_ref=(1-share1)/(1-ph)

    x=ph*cp_ref
    y=(1-ph)*cr_ref

    v_r = ((x+y)*v - x* v_p)/y

    return v_p,v_r, fap_ref, far_ref, cp_ref, cr_ref

def compute_v_fa(df):

    fap = df["fap"]
    far = df["far"]

    vp = df.v_p
    vr=df.v_r

    ph = 0.2#df["pov_head"]

    cp=    df["gdp_pc_pp"]*df["share1"]/ph
    cr= df["gdp_pc_pp"]*(1-df["share1"])/(1-ph)

    fa = ph*fap+(1-ph)*far

    x=ph*cp
    y=(1-ph)*cr

    v=(y*vr+x*vp)/(x+y)

    pv = vp/v-1
    pe = fap/fa-1


    return v,pv,fa,pe



def compute_resilience_from_packed_inputs(df) :

    df=df.copy()
    ##MACRO
    macro_cols = [c for c in df if "macro" in c ]
    macro = df[macro_cols]
    macro = macro.rename(columns=lambda c:c.replace("macro_",""))

    ##CAT INFO
    cat_cols = [c for c in df if "cat_info" in c ]
    cat_info = df[cat_cols]
    cat_info.columns=pd.MultiIndex.from_tuples([c.replace("cat_info_","").split("__") for c in cat_info])
    cat_info = cat_info.sort_index(axis=1).stack()
    cat_info.index.names="name","income_cat"


    ##HAZARD RATIOS
    ###exposure
    fa_cols =  [c for c in df if "hazard_ratio_fa" in c ]
    fa = df[fa_cols]
    fa.columns=[c.replace("hazard_ratio_fa__","") for c in fa]

    ##### add poor and nonpoor
    hop=pd.DataFrame(2*[fa.unstack()], index=["poor","nonpoor"]).T
    hop.ix["flood"]["poor"] = df.hazard_ratio_flood_poor
    hop.ix["surge"]["poor"] = hop.ix["flood"]["poor"] * df["ratio_surge_flood"]
    hop.ix["surge"]["nonpoor"] = hop.ix["flood"]["nonpoor"] * df["ratio_surge_flood"]
    hop=hop.stack().swaplevel(0,1).sort_index()
    hop.index.names=["name","hazard","income_cat"]

    hazard_ratios = pd.DataFrame()
    hazard_ratios["fa"]=hop

    ## Shew
    hazard_ratios["shew"]=0
    # sesha commenting next line and adding next two lines to incorporate multi country values.Needs to be verified by Brian
    #hazard_ratios["shew"] +=df.shew_for_hazard_ratio
    names = hazard_ratios["fa"].index.get_level_values('name') #sesha added
    hazard_ratios["shew"] = df.ix[names]["shew_for_hazard_ratio"].values #sesha added
    #no EW for earthquake
    hazard_ratios["shew"]=hazard_ratios.shew.unstack("hazard").assign(earthquake=0).stack("hazard").reset_index().set_index(["name", "hazard","income_cat"])

    #ACTUALLY DO THE THING
    out = compute_resilience(macro, cat_info, hazard_ratios)

    df[["risk","resilience","risk_to_assets"]] = out[["risk","resilience","risk_to_assets"]]

    return df

#sesha adding this new function
#Alternative to above function but with adjusted macro, cat_info when scorecard policy calculations kick in
def compute_resilience_from_adjusted_inputs_for_pol(df, macro, cat_info, hazard_ratios,optionPDS,optionFee) :
    # ACTUALLY DO THE THING
    out = compute_resilience(macro, cat_info, hazard_ratios,optionPDS=optionPDS,optionFee=optionFee)
    df2 = df.copy()

    #Add these new columns for scorecard metrics output
    df2["dK"] = 0.0
    df2["dKtot"] = 0.0
    df2["delta_W"] = 0.0
    df2["delta_W_tot"] = 0.0
    df2["dWpc_currency"] = 0.0
    df2["dWtot_currency"] = 0.0

    df2[["risk", "resilience", "risk_to_assets","dK","dKtot","delta_W","delta_W_tot","dWpc_currency","dWtot_currency"]] = out[["risk", "resilience", "risk_to_assets","dK","dKtot","delta_W","delta_W_tot","dWpc_currency","dWtot_currency"]]

    #return this dataframe with metrics calculated for a spedific policy
    return df2
